import {
  App,
  applyDocumentTheme,
} from '@modelcontextprotocol/ext-apps';

type FileSummary = { path: string; kind: string };
type DiffChange = { path: string; kind: string; diff: string };
type TurnEvent =
  | { kind: 'commentary'; ts: string; text: string }
  | { kind: 'command'; ts: string; cmd: string; exit_code: number | null; duration_s: number | null; output: string }
  | { kind: 'file_change'; ts: string; changes: DiffChange[] };
type TokenStats = { input: number; cached: number; output: number; reasoning: number; total: number };
type Turn = {
  turn_id: string;
  dispatch_id?: string;
  started_ms: number | null;
  started_fmt: string;
  ended_fmt: string;
  duration_s: number | null;
  status: string;
  model: string;
  dispatcher_model?: string;
  effort: string;
  cwd: string;
  prompt: string;
  final_message: string;
  events: TurnEvent[];
  files: FileSummary[];
  cmd_count: number;
  tokens: TokenStats | null;
};
type Session = {
  session_id: string;
  dispatch_ids?: string[];
  direction?: 'ag_to_codex';
  cwd: string;
  project: string;
  model: string;
  dispatcher_model?: string;
  effort: string;
  updated_fmt: string;
  updated_ms: number | null;
  tokens_used: number;
  status: string;
  turns: Turn[];
};
type DashboardState = {
  generated_at?: string;
  server_time_ms?: number;
  fingerprint?: string;
  focus_session_id?: string;
  focus_dispatch_id?: string;
  opened_at_ms?: number;
  changed?: boolean;
  sessions?: Session[];
};

const isWebMode = window.parent === window;

const app = isWebMode ? null : new App({ name: 'Codex Monitor', version: '2.0.0' }, {});

let state: DashboardState =
  (window as unknown as { __INITIAL_STATE__?: DashboardState }).__INITIAL_STATE__ || { sessions: [] };
let selectedSid: string | null = null;
let autoFollow = true;
let searchQuery = '';
let lastFingerprint = '';
let stateEpoch = 0;
let pollInFlight: number | null = null;
let pinnedTarget: { sessionId?: string; dispatchId?: string } | null = null;
let pinResolved = false;
let runningAtPinRelease: Set<string> | null = null;
let expandedKeys = new Set<string>();
let userToggledTraces = new Map<string, boolean>();
let userToggledGroups = new Map<string, boolean>();
let lastSessionTurnKey = '';
let lastRunningJumpKey = '';
let userPinnedTheme: string | null = null;

const MOON_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.2 12.1A6.5 6.5 0 0 1 7.9 3.8a6.5 6.5 0 1 0 8.3 8.3z"/></svg>';
const SUN_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="3.4"/><path d="M10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7l1.3 1.3M14 14l1.3 1.3M15.3 4.7L14 6M6 14l-1.3 1.3"/></svg>';

const GEMINI_SVG = `<svg class="avatar-svg" viewBox="0 0 24 24" aria-hidden="true">
  <g clip-path="url(#gemStarClip)">
    <rect width="24" height="24" fill="#4285F4"/>
    <circle cx="4" cy="4" r="14" fill="url(#gemRed)"/>
    <circle cx="4" cy="20" r="14" fill="url(#gemYellow)"/>
    <circle cx="20" cy="20" r="13.5" fill="url(#gemGreen)"/>
    <circle cx="20" cy="4" r="14" fill="url(#gemBlue)"/>
  </g>
</svg>`;

const OPENAI_SVG = `<svg class="avatar-svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/></svg>`;

const MINI_GEMINI_SVG = `<svg class="mini-icon" viewBox="0 0 24 24" aria-hidden="true"><g clip-path="url(#gemStarClip)"><rect width="24" height="24" fill="#4285F4"/><circle cx="4" cy="4" r="14" fill="url(#gemRed)"/><circle cx="4" cy="20" r="14" fill="url(#gemYellow)"/><circle cx="20" cy="20" r="13.5" fill="url(#gemGreen)"/><circle cx="20" cy="4" r="14" fill="url(#gemBlue)"/></g></svg>`;
const MINI_OPENAI_SVG = `<svg class="mini-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/></svg>`;
const MINI_ARROW_SVG = `<svg class="mini-arrow" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 6h7M6.8 3.2L9.5 6 6.8 8.8"/></svg>`;

function esc(s: unknown): string {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function relPath(full: string, cwd: string): string {
  if (!cwd) return full;
  const nf = full.replace(/\\/g, '/');
  const nc = cwd.replace(/\\/g, '/').replace(/\/$/, '');
  return nf.toLowerCase().startsWith(nc.toLowerCase() + '/') ? nf.slice(nc.length + 1) : full;
}

function fmtDur(s: number | null | undefined): string {
  if (s == null || Number.isNaN(s)) return '';
  const sec = Math.max(0, Math.floor(s));
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  return m > 0 ? `${m}分${String(r).padStart(2, '0')}秒` : `${r}秒`;
}

function fmtClock(ts: string | undefined): string {
  if (!ts || ts === '-') return '';
  return ts.length >= 16 ? ts.slice(5, 16) : ts;
}

function formatInline(raw: string): string {
  // Extract inline code spans first so markdown syntax inside backticks is untouched
  const codes: string[] = [];
  let text = raw.replace(/`([^`]+)`/g, (_m, code: string) => {
    const idx = codes.length;
    codes.push(`<code class="inline-code">${esc(code)}</code>`);
    return `\u0000CODE${idx}\u0000`;
  });
  text = esc(text);
  // Markdown links [label](<path>) or [label](path)
  text = text.replace(/\[([^\]]+)\]\(&lt;([^&]+)&gt;\)/g, '<code class="inline-code">$1</code>');
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<code class="inline-code">$1</code>');
  // Bold / italic / strikethrough
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  text = text.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');
  text = text.replace(/\u0000CODE(\d+)\u0000/g, (_m, i: string) => codes[Number(i)] || '');
  return text;
}

function splitTableRow(line: string): string[] {
  const s = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  const cells: string[] = [];
  let cur = '';
  let inCode = false;
  for (let i = 0; i < s.length; i += 1) {
    const ch = s[i];
    if (ch === '`') {
      inCode = !inCode;
      cur += ch;
    } else if (ch === '|' && !inCode) {
      cells.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  cells.push(cur.trim());
  return cells;
}

function isTableDivider(line: string): boolean {
  const t = line.trim();
  if (!t.includes('-')) return false;
  return /^\|?(\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?$/.test(t);
}

function formatRichText(text: string): string {
  if (!text) return '';
  const lines = text.replace(/\r\n/g, '\n').trim().split('\n');
  const out: string[] = [];
  const listPattern = /^(\s*)([-*]|\d+\.)\s+(\S.*)$/;
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    i += 1;
    const trimmed = line.trim();

    if (!trimmed) continue;

    // Fenced code block
    const fence = trimmed.match(/^```(\S*)/);
    if (fence) {
      const buf: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      out.push(`<pre class="md-code"><code>${esc(buf.join('\n'))}</code></pre>`);
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      out.push('<hr class="md-hr" />');
      continue;
    }

    // Headings
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const lvl = Math.min(4, heading[1].length);
      out.push(`<h${lvl + 1} class="md-h md-h${lvl}">${formatInline(heading[2])}</h${lvl + 1}>`);
      continue;
    }

    // GFM Table
    if (trimmed.includes('|') && i < lines.length && isTableDivider(lines[i])) {
      const headers = splitTableRow(trimmed);
      i += 1;
      const rows: string[][] = [];
      while (i < lines.length) {
        const rowLine = lines[i].trim();
        if (!rowLine || !rowLine.includes('|')) break;
        rows.push(splitTableRow(rowLine));
        i += 1;
      }
      const thHtml = headers.map((h) => `<th>${formatInline(h)}</th>`).join('');
      const bodyHtml = rows
        .map((r) => `<tr>${headers.map((_h, ci) => `<td>${formatInline(r[ci] ?? '')}</td>`).join('')}</tr>`)
        .join('');
      out.push(
        `<div class="table-wrap"><table class="md-table"><thead><tr>${thHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`
      );
      continue;
    }

    // Blockquote
    if (trimmed.startsWith('>')) {
      const qLines = [trimmed.replace(/^>\s?/, '')];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        qLines.push(lines[i].trim().replace(/^>\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote class="md-quote">${formatInline(qLines.join(' '))}</blockquote>`);
      continue;
    }

    // Unordered or ordered list (with nested indent support)
    let item = line.match(listPattern);
    if (item) {
      const ordered = /^\d+\.$/.test(item[2]);
      const tag = ordered ? 'ol' : 'ul';
      const items: string[] = [];
      while (true) {
        const nested = item[1].length >= 2;
        items.push(`<li${nested ? ' class="nested-li"' : ''}>${formatInline(item[3])}</li>`);
        if (i === lines.length) break;
        item = lines[i].match(listPattern);
        if (!item || (item[1].length === 0 && /^\d+\.$/.test(item[2]) !== ordered)) break;
        i += 1;
      }
      out.push(`<${tag} class="rich-list${ordered ? ' ordered' : ''}">${items.join('')}</${tag}>`);
      continue;
    }

    // Normal paragraph (merge consecutive plain lines, respecting explicit line breaks)
    const para = [formatInline(trimmed)];
    while (i < lines.length) {
      const cur = lines[i];
      const ct = cur.trim();
      if (
        !ct ||
        ct.startsWith('```') ||
        ct.startsWith('>') ||
        /^(#{1,4})\s+/.test(ct) ||
        /^(-{3,}|\*{3,}|_{3,})$/.test(ct) ||
        listPattern.test(cur) ||
        (ct.includes('|') && i + 1 < lines.length && isTableDivider(lines[i + 1]))
      ) {
        break;
      }
      para.push(formatInline(ct));
      i += 1;
    }
    out.push(`<p>${para.join('<br />')}</p>`);
  }
  return out.join('');
}

function renderDiff(diff: string): string {
  if (!diff) return '<div class="diff-line muted">(无 diff)</div>';
  return diff
    .split('\n')
    .map((ln) => {
      let cls = 'diff-line';
      if (ln.startsWith('@@')) cls += ' diff-hunk';
      else if (ln.startsWith('+') && !ln.startsWith('+++')) cls += ' diff-add';
      else if (ln.startsWith('-') && !ln.startsWith('---')) cls += ' diff-del';
      return `<div class="${cls}">${esc(ln)}</div>`;
    })
    .join('');
}

function captureExpanded(): void {
  expandedKeys.clear();
  document.querySelectorAll<HTMLDetailsElement>('details[data-key]').forEach((el) => {
    const k = el.dataset.key;
    if (!k) return;
    if (el.open) expandedKeys.add(k);
    if (el.classList.contains('trace-drawer')) {
      userToggledTraces.set(k, el.open);
    } else if (el.classList.contains('proj-group')) {
      userToggledGroups.set(k, el.open);
    }
  });
}

function setLiveBadge(live: boolean, label: string): void {
  const pill = document.getElementById('live-pill');
  const txt = document.getElementById('live-text');
  if (!pill || !txt) return;
  pill.classList.toggle('offline', !live);
  txt.textContent = label;
}

function fmtCompactTok(n: number): string {
  if (!n) return '';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function applyTheme(theme: string | undefined): void {
  const mode = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', mode);
  const themeBtn = document.getElementById('theme-btn');
  if (themeBtn) {
    themeBtn.innerHTML = mode === 'dark' ? SUN_SVG : MOON_SVG;
    themeBtn.title = mode === 'dark' ? '切换浅色主题' : '切换深色主题';
  }
  if (app) {
    try {
      applyDocumentTheme(mode);
    } catch {
      // Ignore
    }
  }
}

function matchesSearch(s: Session): boolean {
  const q = searchQuery.trim().toLowerCase();
  if (!q) return true;
  const blob = s.project + ' ' + s.cwd + ' ' + s.model + ' ' + s.turns.map((t) => t.prompt + ' ' + t.final_message).join(' ');
  return blob.toLowerCase().includes(q);
}

function matchesDispatch(session: Session, dispatchId: string): boolean {
  return session.session_id === dispatchId || !!session.dispatch_ids?.includes(dispatchId)
    || session.turns.some((turn) => turn.dispatch_id === dispatchId);
}

function findPinnedSession(sessions: Session[]): Session | undefined {
  if (!pinnedTarget) return;
  const { dispatchId, sessionId } = pinnedTarget;
  return (dispatchId ? sessions.find((s) => matchesDispatch(s, dispatchId)) : undefined)
    || sessions.find((s) => s.session_id === sessionId);
}

function runningKey(session: Session): string {
  const turn = session.turns[session.turns.length - 1];
  return turn?.dispatch_id ? `dispatch:${turn.dispatch_id}` : `${session.session_id}:${turn?.turn_id || ''}`;
}

function findPinnedTurn(session: Session, dispatchId?: string): Turn | undefined {
  const latest = session.turns[session.turns.length - 1];
  if (!dispatchId) return latest;
  return session.turns.find((turn) => turn.dispatch_id === dispatchId)
    || (session.session_id === dispatchId && !latest?.dispatch_id ? latest : undefined);
}

function selectSession(sessionId: string | null): void {
  pinnedTarget = null;
  runningAtPinRelease = null;
  const running = state.sessions?.find((s) => s.status === 'running' && matchesSearch(s));
  lastRunningJumpKey = running ? runningKey(running) : '';
  selectedSid = sessionId;
  render();
}

function render(focusedTurn?: Turn): void {
  captureExpanded();
  const sessions = state.sessions || [];
  const list = sessions.filter(matchesSearch);
  const pinned = findPinnedSession(sessions);
  const running = list.find((s) => s.status === 'running' && !runningAtPinRelease?.has(runningKey(s)));
  const nextRunningKey = running ? runningKey(running) : '';

  if (pinned && matchesSearch(pinned)) {
    selectedSid = pinned.session_id;
  } else if (autoFollow && !pinnedTarget && !focusedTurn) {
    if (running && nextRunningKey !== lastRunningJumpKey) {
      runningAtPinRelease = null;
      lastRunningJumpKey = nextRunningKey;
      selectedSid = running.session_id;
    } else if (!selectedSid || !list.some((s) => s.session_id === selectedSid)) {
      selectedSid = (running || list[0])?.session_id ?? null;
    }
  } else if (!selectedSid || !list.some((s) => s.session_id === selectedSid)) {
    selectedSid = list[0]?.session_id ?? null;
  }

  // Group sessions by project directory (most recently active project first)
  type ProjectGroup = { key: string; name: string; cwd: string; hasRunning: boolean; sessions: Session[] };
  const groups: ProjectGroup[] = [];
  const groupMap = new Map<string, ProjectGroup>();
  for (const s of list) {
    const gKey = (s.cwd || s.project || 'unknown').toLowerCase();
    let g = groupMap.get(gKey);
    if (!g) {
      g = { key: gKey, name: s.project || '未命名项目', cwd: s.cwd || '', hasRunning: false, sessions: [] };
      groupMap.set(gKey, g);
      groups.push(g);
    }
    if (s.status === 'running') g.hasRunning = true;
    g.sessions.push(s);
  }

  // Session switcher (compact bar) grouped with optgroup
  const sessSel = document.getElementById('sess-select') as HTMLSelectElement | null;
  if (sessSel) {
    sessSel.innerHTML = groups.length
      ? groups
          .map(
            (g) =>
              `<optgroup label="${esc(g.name)} (${g.sessions.length})">${g.sessions
                .map((s) => {
                  const mark = s.status === 'running' ? '● ' : '';
                  const firstLine = ((s.turns[0]?.prompt || '').split('\n')[0] || 'Codex 会话').slice(0, 32);
                  return `<option value="${esc(s.session_id)}"${s.session_id === selectedSid ? ' selected' : ''}>${mark}${esc(firstLine)} · ${s.turns.length}轮</option>`;
                })
                .join('')}</optgroup>`
          )
          .join('')
      : '<option value="">暂无对话</option>';
  }

  // Wide sidebar: collapsible project groups
  const sideEl = document.getElementById('side-list');
  if (sideEl) {
    sideEl.innerHTML = groups.length
      ? groups
          .map((g, gi) => {
            const dKey = `proj:${g.key}`;
            const containsSelected = g.sessions.some((s) => s.session_id === selectedSid);
            const isOpen = userToggledGroups.has(dKey)
              ? Boolean(userToggledGroups.get(dKey))
              : containsSelected || g.hasRunning || gi === 0;
            const itemsHtml = g.sessions
              .map((s) => {
                const firstPrompt = ((s.turns[0]?.prompt || '').split('\n')[0] || '协作会话').trim();
                const dirIcons = `<span class="dir-icons" title="Antigravity → Codex">${MINI_GEMINI_SVG}${MINI_ARROW_SVG}${MINI_OPENAI_SVG}</span>`;
                return `
                  <button class="side-item ${s.session_id === selectedSid ? 'active' : ''}" data-sid="${esc(s.session_id)}">
                    <div class="side-item-head">
                      <span class="side-title">${esc(firstPrompt)}</span>
                      ${s.status === 'running' ? '<span class="status-dot running"></span>' : '<span class="status-dot done"></span>'}
                    </div>
                    <div class="side-meta">${dirIcons}<span class="side-meta-txt">${esc(s.model)} · ${s.turns.length}轮 · ${esc(fmtClock(s.updated_fmt))}</span></div>
                  </button>`;
              })
              .join('');

            return `
              <details class="proj-group" data-key="${esc(dKey)}" ${isOpen ? 'open' : ''}>
                <summary class="proj-head" title="${esc(g.cwd)}">
                  <svg class="proj-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>
                  <svg class="proj-folder" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M1.8 4.2a1.2 1.2 0 0 1 1.2-1.2h3.1l1.3 1.4H13a1.2 1.2 0 0 1 1.2 1.2v6.2A1.2 1.2 0 0 1 13 13H3a1.2 1.2 0 0 1-1.2-1.2V4.2z"/></svg>
                  <span class="proj-name">${esc(g.name)}</span>
                  ${g.hasRunning ? '<span class="status-dot running"></span>' : ''}
                  <span class="proj-count">${g.sessions.length}</span>
                </summary>
                <div class="proj-items">${itemsHtml}</div>
              </details>`;
          })
          .join('')
      : '<div class="empty-side">暂无历史派单</div>';

    sideEl.querySelectorAll<HTMLButtonElement>('.side-item').forEach((btn) => {
      btn.onclick = () => selectSession(btn.dataset.sid || null);
    });
  }

  const sess = list.find((s) => s.session_id === selectedSid);
  const feedEl = document.getElementById('chat-feed');
  const metaEl = document.getElementById('thread-meta');
  if (!feedEl) return;

  if (!sess) {
    if (metaEl) metaEl.innerHTML = '';
    feedEl.innerHTML = `
      <div class="empty-chat">
        <div class="empty-emblem">✦</div>
        <div class="empty-title">还没有派发任务给 Codex</div>
        <div class="empty-sub">当 Antigravity 给 Codex 派单时，这里会以双人对话形式实时呈现指令、执行过程与回复。</div>
      </div>`;
    return;
  }

  const latest = sess.turns[sess.turns.length - 1];
  const dispatchId = pinnedTarget?.dispatchId;
  const activeTurn = focusedTurn || (pinned === sess ? findPinnedTurn(sess, dispatchId) : undefined) || latest;
  const tok = latest?.tokens;
  const cachedPct = tok && tok.input ? Math.round((tok.cached / tok.input) * 100) : 0;

  if (metaEl) {
    const dirLabel = 'Antigravity → Codex';
    const subBits = [
      dirLabel,
      `${sess.model}${sess.effort ? ' · ' + sess.effort : ''}`,
      `${sess.turns.length} 轮`,
    ];
    if (tok && tok.total) {
      subBits.push(`${fmtCompactTok(tok.total)} tokens · ${cachedPct}% 缓存`);
    }
    metaEl.innerHTML = `
      <div class="thread-title-group" title="${esc(sess.cwd)}">
        <div class="thread-title-row">
          <span class="thread-name">${esc(sess.project)}</span>
          ${sess.status === 'running' ? '<span class="header-run-dot" title="执行中"></span>' : ''}
        </div>
        <div class="thread-sub">${esc(subBits.join('  ·  '))}</div>
      </div>`;
  }

  const blocks = sess.turns
    .map((t, idx) => {
      const tKey = `${sess.session_id}:${t.turn_id}`;
      const isRun = t.status === 'running';

      // Fixed speaker positions: Codex is ALWAYS on the LEFT, Antigravity is ALWAYS on the RIGHT
      const dispatcherName = 'Antigravity';
      const dispatcherAvatar = GEMINI_SVG;
      const dispatcherAvatarCls = 'avatar-ag';
      const dispatcherOnLeft = false;

      const executorName = 'Codex';
      const executorAvatar = OPENAI_SVG;
      const executorAvatarCls = 'avatar-cx';
      const executorOnRight = false;

      // --- 1. Dispatcher bubble (left if Codex, right if Antigravity) ---
      const promptLines = (t.prompt || '').trim().split('\n');
      const isLongPrompt = promptLines.length > 7 || (t.prompt || '').length > 420;
      const previewText = isLongPrompt ? promptLines.slice(0, 6).join('\n') : t.prompt;
      const pKey = `${tKey}:fullprompt`;
      const promptOpen = expandedKeys.has(pKey);

      const dispatchSpeaker = dispatcherOnLeft
        ? `<div class="speaker-line">
             <span class="avatar ${dispatcherAvatarCls}" title="${dispatcherName}">${dispatcherAvatar}</span>
             <span class="speaker-name">${dispatcherName}</span>
             ${t.dispatcher_model ? `<span class="model-tag">${esc(t.dispatcher_model)}</span>` : ''}
             <span class="round-tag">第 ${idx + 1} 轮派单</span>
             <span class="speaker-time">${esc(fmtClock(t.started_fmt))}</span>
           </div>`
        : `<div class="speaker-line right">
             <span class="speaker-time">${esc(fmtClock(t.started_fmt))}</span>
             <span class="round-tag">第 ${idx + 1} 轮派单</span>
             ${t.dispatcher_model ? `<span class="model-tag">${esc(t.dispatcher_model)}</span>` : ''}
             <span class="speaker-name">${dispatcherName}</span>
             <span class="avatar ${dispatcherAvatarCls}" title="${dispatcherName}">${dispatcherAvatar}</span>
           </div>`;

      const agBubble = `
        <div class="msg-row ${dispatcherOnLeft ? 'from-left' : 'from-right'}">
          <div class="msg-col ${dispatcherOnLeft ? 'left-dispatch-col' : 'ag-col'}">
            ${dispatchSpeaker}
            <div class="bubble-dispatch ${dispatcherOnLeft ? 'bubble-left' : 'bubble-right'}">
              ${
                isLongPrompt
                  ? `<div class="rich-body">${formatRichText(previewText)}</div>
                     <details class="more-prompt" data-key="${esc(pKey)}" ${promptOpen ? 'open' : ''}>
                       <summary>${promptOpen ? '收起完整派单内容' : `展开完整派单内容（共 ${promptLines.length} 行）`}</summary>
                       <pre class="prompt-full">${esc(t.prompt)}</pre>
                     </details>`
                  : `<div class="rich-body">${formatRichText(t.prompt || '(等待指令)')}</div>`
              }
            </div>
          </div>
        </div>`;

      // --- 2. Codex (left-side Claude-style editorial response + collapsible execution drawer) ---
      const traceKey = `${tKey}:trace`;
      // Default open while running, cleanly collapsed when completed (unless user explicitly toggled)
      const traceOpen = userToggledTraces.has(traceKey) ? Boolean(userToggledTraces.get(traceKey)) : isRun;

      const durHtml =
        isRun && t.started_ms
          ? `<span class="live-timer" data-start="${t.started_ms}">${fmtDur((Date.now() - t.started_ms) / 1000)}</span>`
          : fmtDur(t.duration_s);

      // Deduplicate touched files client-side as well (preserve 'add' over subsequent 'update')
      const dedupFiles: FileSummary[] = [];
      for (const f of t.files || []) {
        const prev = dedupFiles.find((x) => x.path.toLowerCase() === f.path.toLowerCase());
        if (!prev) dedupFiles.push({ ...f });
        else if (f.kind === 'delete') prev.kind = 'delete';
        else if (prev.kind !== 'add') prev.kind = f.kind;
      }

      const terminalBadge = t.status === 'failed'
        ? '<span class="step-pill st-err">执行失败</span>'
        : t.status === 'aborted'
          ? '<span class="step-pill st-run">已中止</span>'
          : '';
      const summaryBits = [terminalBadge || (isRun ? '正在执行' : '已完成执行')];
      if (t.cmd_count > 0) summaryBits.push(`${t.cmd_count} 条命令`);
      if (dedupFiles.length > 0) summaryBits.push(`${dedupFiles.length} 个文件`);
      if (durHtml) summaryBits.push(durHtml);

      const stepsHtml = (t.events || [])
        .map((ev, eIdx) => {
          const eKey = `${tKey}:ev:${eIdx}`;
          if (ev.kind === 'commentary') {
            return `
              <div class="step step-note">
                <span class="step-icon">💭</span>
                <div class="step-text">${formatRichText(ev.text)}</div>
              </div>`;
          }
          if (ev.kind === 'command') {
            const ok = ev.exit_code === 0;
            const stCls = ev.exit_code == null ? 'st-run' : ok ? 'st-ok' : 'st-err';
            const stText = ev.exit_code == null ? '运行中' : ok ? '通过' : `退出 ${ev.exit_code}`;
            return `
              <div class="step">
                <details class="step-box" data-key="${esc(eKey)}" ${expandedKeys.has(eKey) ? 'open' : ''}>
                  <summary>
                    <span class="step-icon mono">$</span>
                    <span class="cmd-preview">${esc(ev.cmd)}</span>
                    <span class="step-pill ${stCls}">${stText}${ev.duration_s != null ? ' · ' + ev.duration_s + 's' : ''}</span>
                  </summary>
                  <pre class="step-out">${esc(ev.output || '(无终端输出)')}</pre>
                </details>
              </div>`;
          }
          if (ev.kind === 'file_change') {
            return (ev.changes || [])
              .map((ch, cIdx) => {
                const cKey = `${eKey}:f:${cIdx}`;
                const kLabel = ch.kind === 'add' ? '新建' : ch.kind === 'delete' ? '删除' : '修改';
                const kCls = ch.kind === 'add' ? 'k-add' : ch.kind === 'delete' ? 'k-del' : 'k-upd';
                return `
                  <div class="step">
                    <details class="step-box" data-key="${esc(cKey)}" ${expandedKeys.has(cKey) ? 'open' : ''}>
                      <summary>
                        <span class="file-kind ${kCls}">${kLabel}</span>
                        <span class="file-name">${esc(relPath(ch.path, sess.cwd))}</span>
                        <span class="diff-hint">查看变更 ▾</span>
                      </summary>
                      <div class="diff-view">${renderDiff(ch.diff)}</div>
                    </details>
                  </div>`;
              })
              .join('');
          }
          return '';
        })
        .join('');

      const fileChipsHtml =
        dedupFiles.length > 0
          ? `<div class="touched-row">
              ${dedupFiles
                .map((f) => {
                  const kLabel = f.kind === 'add' ? '+' : f.kind === 'delete' ? '−' : '~';
                  const kCls = f.kind === 'add' ? 'k-add' : f.kind === 'delete' ? 'k-del' : 'k-upd';
                  return `<span class="touched-chip"><span class="chip-mark ${kCls}">${kLabel}</span><span>${esc(relPath(f.path, sess.cwd))}</span></span>`;
                })
                .join('')}
            </div>`
          : '';

      const latestCommentary = [...(t.events || [])].reverse().find((e) => e.kind === 'commentary') as
        | { kind: 'commentary'; text: string }
        | undefined;

      const execSpeaker = executorOnRight
        ? `<div class="speaker-line right">
             ${isRun ? '<span class="live-chip"><span class="pulse-dot"></span>执行中</span>' : ''}
             <span class="model-tag">${esc(t.model || sess.model)}${t.effort ? ' · ' + esc(t.effort) : ''}</span>
             <span class="speaker-name">${executorName}</span>
             <span class="avatar ${executorAvatarCls}" title="${executorName}">${executorAvatar}</span>
           </div>`
        : `<div class="speaker-line">
             <span class="avatar ${executorAvatarCls}" title="${executorName}">${executorAvatar}</span>
             <span class="speaker-name">${executorName}</span>
             <span class="model-tag">${esc(t.model || sess.model)}${t.effort ? ' · ' + esc(t.effort) : ''}</span>
             ${isRun ? '<span class="live-chip"><span class="pulse-dot"></span>执行中</span>' : ''}
           </div>`;

      const codexBubble = `
        <div class="msg-row ${executorOnRight ? 'from-right' : 'from-left'}">
          <div class="msg-col ${executorOnRight ? 'exec-right-col' : 'codex-col'}">
            ${execSpeaker}

            <div class="exec-content ${executorOnRight ? 'exec-align-right' : 'exec-align-left'}">
              ${
                (t.events && t.events.length > 0) || isRun
                  ? `<details class="trace-drawer ${isRun ? 'is-running' : ''}" data-key="${esc(traceKey)}" ${traceOpen ? 'open' : ''}>
                      <summary class="trace-summary">
                        <span class="trace-icon">${isRun ? '<span class="spinner"></span>' : '✦'}</span>
                        <span class="trace-label">${summaryBits.join(' · ')}</span>
                        <span class="trace-chevron">▾</span>
                      </summary>
                      <div class="trace-body">
                        ${stepsHtml || '<div class="step-wait">正在等待返回第一步动作...</div>'}
                      </div>
                    </details>`
                  : terminalBadge
              }

              ${
                isRun && latestCommentary
                  ? `<div class="live-whisper">${esc(latestCommentary.text)}</div>`
                  : ''
              }

              ${
                t.final_message
                  ? `<div class="reply-card ${executorOnRight ? 'reply-right' : 'reply-left'}">
                      <div class="rich-body">${formatRichText(t.final_message)}</div>
                      ${fileChipsHtml}
                    </div>`
                  : isRun
                    ? `<div class="thinking-line"><span class="typing-dots"><span></span><span></span><span></span></span><span>${executorName} 正在整理执行结果...</span></div>`
                    : fileChipsHtml
              }
            </div>
          </div>
        </div>`;

      return `<div class="round-pair">${agBubble}${codexBubble}</div>`;
    })
    .join('');

  feedEl.innerHTML = blocks;

  // Scroll gently to bottom when a new turn arrives or while running in autoFollow
  const turnKey = `${sess.session_id}:${activeTurn?.turn_id || ''}:${activeTurn?.events?.length || 0}:${activeTurn?.status || ''}`;
  if (autoFollow && turnKey !== lastSessionTurnKey) {
    lastSessionTurnKey = turnKey;
    const scroller = document.getElementById('chat-scroll');
    if (scroller) {
      if (activeTurn && activeTurn !== latest) {
        feedEl.children.item(sess.turns.indexOf(activeTurn))?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
      }
    }
  }
}

function isDashboardState(value: unknown): value is DashboardState {
  if (!value || typeof value !== 'object') return false;
  const data = value as DashboardState;
  if (data.changed === false) return typeof data.fingerprint === 'string' && !!data.fingerprint;
  if ([data.fingerprint, data.focus_session_id, data.focus_dispatch_id].some((id) => id !== undefined && typeof id !== 'string')) return false;
  return Array.isArray(data.sessions) && data.sessions.every((session) =>
    session && typeof session.session_id === 'string' && Array.isArray(session.turns)
    && (session.dispatch_ids === undefined || (Array.isArray(session.dispatch_ids) && session.dispatch_ids.every((id) => typeof id === 'string')))
    && session.turns.every((turn) => turn && typeof turn.turn_id === 'string' && typeof turn.status === 'string'
      && typeof turn.prompt === 'string' && typeof turn.final_message === 'string'
      && Array.isArray(turn.files) && turn.files.every((file) => file && typeof file.path === 'string')
      && Array.isArray(turn.events) && turn.events.every((event) => event && typeof event.kind === 'string'
        && (event.kind !== 'commentary' || typeof event.text === 'string')
        && (event.kind !== 'file_change' || (Array.isArray(event.changes) && event.changes.every((change) =>
          change && typeof change.path === 'string' && typeof change.diff === 'string'))))));
}

function applyIncoming(next: DashboardState): void {
  if (next.fingerprint) lastFingerprint = next.fingerprint;
  if (next.changed === false) return;
  state = next;
  const explicitFocus = !!(next.focus_session_id || next.focus_dispatch_id);
  if (explicitFocus) {
    pinnedTarget = { sessionId: next.focus_session_id, dispatchId: next.focus_dispatch_id };
    pinResolved = false;
    runningAtPinRelease = null;
  }
  const sessions = next.sessions || [];
  const pinned = findPinnedSession(sessions);
  let focusedTurn: Turn | undefined;
  if (pinned && pinnedTarget) {
    const dispatchId = pinnedTarget.dispatchId;
    const exact = !dispatchId || matchesDispatch(pinned, dispatchId);
    if ((explicitFocus || (exact && !pinResolved)) && !matchesSearch(pinned)) {
      searchQuery = '';
      const search = document.getElementById('side-search') as HTMLInputElement | null;
      if (search) search.value = '';
    }
    if (exact) pinResolved = true;
    const targetTurn = findPinnedTurn(pinned, dispatchId);
    if (targetTurn && ['completed', 'failed', 'aborted'].includes(targetTurn.status)) {
      selectedSid = pinned.session_id;
      if (matchesSearch(pinned)) focusedTurn = targetTurn;
      pinnedTarget = null;
      runningAtPinRelease = new Set(sessions.filter((s) => s.status === 'running').map(runningKey));
    }
  }
  render(focusedTurn);
}

async function pollNow(): Promise<void> {
  const epoch = stateEpoch;
  if (pollInFlight === epoch) return;
  pollInFlight = epoch;
  try {
    let data: unknown;
    if (isWebMode) {
      const resp = await fetch('/api/state', { cache: 'no-store' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      data = await resp.json();
    } else if (app) {
      const res = await app.callServerTool({
        name: 'get_codex_state',
        arguments: {
          last_fingerprint: lastFingerprint,
          limit: 15,
          session_id: pinnedTarget?.sessionId || (runningAtPinRelease ? selectedSid || '' : ''),
          dispatch_id: pinnedTarget?.dispatchId || '',
        },
      });
      if (res.isError) throw new Error('Tool error');
      data = res.structuredContent;
    }
    if (epoch !== stateEpoch) return;
    if (!isDashboardState(data)) throw new Error('Invalid dashboard state');
    applyIncoming(data);
    setLiveBadge(true, '实时同步');
  } catch {
    if (epoch === stateEpoch) setLiveBadge(false, '重连中');
  } finally {
    if (pollInFlight === epoch) pollInFlight = null;
  }
}

function syncFollowUi(): void {
  const btn = document.getElementById('follow-btn');
  if (!btn) return;
  btn.classList.toggle('is-on', autoFollow);
  btn.setAttribute('aria-pressed', String(autoFollow));
}

function wireControls(): void {
  const followBtn = document.getElementById('follow-btn');
  if (followBtn) {
    syncFollowUi();
    followBtn.onclick = () => {
      pinnedTarget = null;
      runningAtPinRelease = null;
      autoFollow = !autoFollow;
      syncFollowUi();
      render();
    };
  }
  const sessSel = document.getElementById('sess-select') as HTMLSelectElement | null;
  if (sessSel) {
    sessSel.onchange = () => selectSession(sessSel.value || null);
  }
  const searchEl = document.getElementById('side-search') as HTMLInputElement | null;
  if (searchEl) {
    searchEl.oninput = () => {
      searchQuery = searchEl.value;
      render();
    };
  }
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) {
    refreshBtn.onclick = () => {
      refreshBtn.classList.add('spinning');
      setTimeout(() => refreshBtn.classList.remove('spinning'), 520);
      lastFingerprint = '';
      void pollNow();
    };
  }
  const themeBtn = document.getElementById('theme-btn');
  if (themeBtn) {
    themeBtn.onclick = () => {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      userPinnedTheme = next;
      applyTheme(next);
      try {
        localStorage.setItem('ag_cx_theme', next);
      } catch {
        // Ignore
      }
    };
  }
}

if (app) {
  app.ontoolinput = () => {
    setLiveBadge(true, '同步中');
  };
  app.ontoolresult = (result) => {
    stateEpoch += 1;
    if (result.isError || !isDashboardState(result.structuredContent) || result.structuredContent.changed === false) {
      setLiveBadge(false, '重连中');
      return;
    }
    autoFollow = true;
    pinnedTarget = null;
    runningAtPinRelease = null;
    lastSessionTurnKey = '';
    lastRunningJumpKey = '';
    syncFollowUi();
    applyIncoming(result.structuredContent);
    setLiveBadge(true, '实时同步');
  };
  app.onhostcontextchanged = (ctx) => {
    if (ctx.theme && !userPinnedTheme) applyTheme(ctx.theme);
  };
}

async function boot(): Promise<void> {
  wireControls();
  const params = new URLSearchParams(location.search);
  if (params.get('demo') === '1') {
    const demo1: Session = {
      session_id: 'demo-1',
      cwd: '/workspace/auth-service',
      project: 'auth-service',
      model: 'gpt-6.2-sol',
      dispatcher_model: 'gemini-4-neon-high',
      effort: 'high',
      updated_fmt: '2026-11-26 10:15:00',
      updated_ms: Date.now(),
      tokens_used: 18320,
      status: 'completed',
      turns: [{
        turn_id: 'demo-1',
        started_ms: Date.now() - 38000,
        started_fmt: '2026-11-26 10:14:22',
        ended_fmt: '2026-11-26 10:15:00',
        duration_s: 38,
        status: 'completed',
        model: 'gpt-6.2-sol',
        dispatcher_model: 'gemini-4-neon-high',
        effort: 'high',
        cwd: '/workspace/auth-service',
        prompt: 'Refactor token refresh single-flight lock in `src/auth/token_refresh.ts` so concurrent 401 responses share one refresh call, then run `npm test`.',
        final_message: 'Updated `src/auth/token_refresh.ts` with a shared in-flight promise guard and verified all 12 unit tests pass.',
        events: [
          { kind: 'commentary', ts: '10:14', text: 'Inspecting existing token cache and concurrent 401 refresh path.' },
          {
            kind: 'file_change',
            ts: '10:14',
            changes: [{
              path: 'src/auth/token_refresh.ts',
              kind: 'update',
              diff: '@@ -14,6 +14,11 @@ export async function getValidToken(): Promise<string> {\n-  if (cached && cached.expiresAt > Date.now()) return cached.value;\n-  return await fetchFreshToken();\n+  if (cached && cached.expiresAt > Date.now() + 5000) {\n+    return cached.value;\n+  }\n+  if (!inFlightRefresh) {\n+    inFlightRefresh = fetchFreshToken().finally(() => { inFlightRefresh = null; });\n+  }\n+  return await inFlightRefresh;\n }',
            }],
          },
          {
            kind: 'command',
            ts: '10:15',
            cmd: 'npm test -- tests/token_refresh.test.ts',
            exit_code: 0,
            duration_s: 1.8,
            output: 'PASS tests/token_refresh.test.ts\n  ✓ deduplicates concurrent 401 refresh calls (14 ms)\n  ✓ clears in-flight lock after network failure (6 ms)\nTests: 12 passed, 12 total',
          },
        ],
        files: [{ path: 'src/auth/token_refresh.ts', kind: 'update' }],
        cmd_count: 1,
        tokens: { input: 15200, cached: 12400, output: 3120, reasoning: 640, total: 18320 },
      }],
    };
    const demo2: Session = {
      session_id: 'demo-2',
      cwd: '/workspace/cache-parser',
      project: 'cache-parser',
      model: 'gpt-6.2-sol',
      dispatcher_model: 'gemini-4-neon-high',
      effort: 'medium',
      updated_fmt: '2026-11-26 09:42:10',
      updated_ms: Date.now() - 1800000,
      tokens_used: 9640,
      status: 'completed',
      turns: [{
        turn_id: 'demo-2',
        started_ms: Date.now() - 1824000,
        started_fmt: '2026-11-26 09:41:46',
        ended_fmt: '2026-11-26 09:42:10',
        duration_s: 24,
        status: 'completed',
        model: 'gpt-6.2-sol',
        dispatcher_model: 'gemini-4-neon-high',
        effort: 'medium',
        cwd: '/workspace/cache-parser',
        prompt: 'Add edge-case unit tests for empty WAL snapshots in `tests/test_parser.py`.',
        final_message: 'Added 4 edge-case tests and confirmed the test suite passes.',
        events: [],
        files: [{ path: 'tests/test_parser.py', kind: 'update' }],
        cmd_count: 1,
        tokens: { input: 8100, cached: 6480, output: 1540, reasoning: 220, total: 9640 },
      }],
    };
    state = { sessions: [demo1, demo2] };
    userToggledTraces.set('demo-1:demo-1:trace', true);
    expandedKeys.add('demo-1:demo-1:ev:1:f:0');
    expandedKeys.add('demo-1:demo-1:ev:2');
    applyTheme(params.get('theme') === 'dark' ? 'dark' : 'light');
    setLiveBadge(true, '演示模式');
    render();
    return;
  }
  render();

  if (isWebMode) {
    let saved = 'light';
    try {
      saved = localStorage.getItem('ag_cx_theme') || 'light';
    } catch {
      saved = 'light';
    }
    applyTheme(saved);
    if (!state.sessions || !state.sessions.length) {
      await pollNow();
    }
    setInterval(() => void pollNow(), 800);
  } else if (app) {
    await app.connect();
    const ctx = app.getHostContext();
    applyTheme(ctx?.theme);
    await pollNow();
    setInterval(() => void pollNow(), 800);
  }

  setInterval(() => {
    document.querySelectorAll<HTMLElement>('.live-timer').forEach((el) => {
      const start = Number(el.dataset.start);
      if (start) el.textContent = fmtDur((Date.now() - start) / 1000);
    });
  }, 1000);
}

void boot();
