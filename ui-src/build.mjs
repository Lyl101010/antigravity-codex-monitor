import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import esbuild from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here, '..', 'plugin', 'ui');
fs.mkdirSync(outDir, { recursive: true });

const built = await esbuild.build({
  stdin: { contents: fs.readFileSync(path.join(here, 'app.ts'), 'utf8'), resolveDir: here, sourcefile: 'app.ts', loader: 'ts' },
  tsconfigRaw: {},
  plugins: [{name: 'local-sdk', setup(build) {
    build.onResolve({ filter: /^@modelcontextprotocol\/ext-apps$/ }, () => ({path: 'sdk', namespace: 'local-sdk'}));
    build.onLoad({ filter: /.*/, namespace: 'local-sdk' }, () => ({contents: fs.readFileSync(path.join(here, 'node_modules/@modelcontextprotocol/ext-apps/dist/src/app-with-deps.js'), 'utf8'), loader: 'js'}));
  }}],
  bundle: true,
  format: 'iife',
  target: 'es2020',
  minify: true,
  write: false,
});

const js = built.outputFiles[0].text.replace(/<\/script>/gi, '<\\/script>');

const css = `
:root, [data-theme="light"] {
  --canvas: #faf9f5;
  --surface: #ffffff;
  --surface-warm: #f3f1ea;
  --surface-subtle: #f6f4ee;
  --bubble-ag: #efece3;
  --bubble-ag-border: #e3dfd2;
  --border: #e7e4db;
  --border-soft: #efede6;
  --ink: #24221f;
  --ink-2: #66625a;
  --ink-3: #8d887e;
  --clay: #c96442;
  --clay-soft: rgba(201, 100, 66, 0.11);
  --ag-accent: #5268a8;
  --ag-soft: rgba(82, 104, 168, 0.11);
  --sage: #2e7d5b;
  --sage-soft: rgba(46, 125, 91, 0.11);
  --amber: #b36b1d;
  --amber-soft: rgba(179, 107, 29, 0.12);
  --crimson: #c2413a;
  --crimson-soft: rgba(194, 65, 58, 0.11);
  --code-bg: #f4f2ec;
  --shadow-sm: 0 1px 2px rgba(28, 25, 21, 0.04);
  --sans: "Noto Sans SC", "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
  --serif: "Georgia", "Sitka Text", "Noto Serif SC", "STSong", serif;
  --mono: "Cascadia Code", "JetBrains Mono", ui-monospace, Consolas, monospace;
}

[data-theme="dark"] {
  --canvas: #1c1b18;
  --surface: #252420;
  --surface-warm: #2c2a25;
  --surface-subtle: #21201c;
  --bubble-ag: #2d2b25;
  --bubble-ag-border: #3a372f;
  --border: #33312b;
  --border-soft: #2a2823;
  --ink: #eceae3;
  --ink-2: #a5a096;
  --ink-3: #7c776e;
  --clay: #de7d5c;
  --clay-soft: rgba(222, 125, 92, 0.15);
  --ag-accent: #8ca4e8;
  --ag-soft: rgba(140, 164, 232, 0.14);
  --sage: #58b88d;
  --sage-soft: rgba(88, 184, 141, 0.14);
  --amber: #e3a24c;
  --amber-soft: rgba(227, 162, 76, 0.15);
  --crimson: #e0716a;
  --crimson-soft: rgba(224, 113, 106, 0.15);
  --code-bg: #181714;
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.22);
}

* { box-sizing: border-box; }
html, body {
  margin: 0; height: 100%;
  background: var(--canvas); color: var(--ink);
  font-family: var(--sans); font-size: 13.5px; line-height: 1.62;
  -webkit-font-smoothing: antialiased;
}

.shell {
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  height: 100vh;
  overflow: hidden;
}
.compact-switcher { display: none; }
@media (max-width: 780px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .compact-switcher {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 14px 4px; background: var(--canvas);
  }
}

/* Full-height Claude-style warm sidebar */
.sidebar {
  background: var(--surface-subtle);
  border-right: 1px solid var(--border-soft);
  display: flex; flex-direction: column; overflow: hidden;
}
.side-brand {
  display: flex; align-items: center; gap: 11px;
  padding: 15px 14px 12px;
}
.brand-medallions {
  display: inline-flex; align-items: center; flex-shrink: 0;
}
.medal {
  width: 38px; height: 38px; border-radius: 999px;
  background: #ffffff;
  border: 1px solid var(--border);
  box-shadow: 0 2px 6px rgba(28, 25, 21, 0.07);
  display: grid; place-items: center;
  position: relative;
  overflow: hidden;
}
[data-theme="dark"] .medal-gpt { background: var(--surface); }
.medal-ag { z-index: 2; }
.medal-gpt {
  margin-left: -10px; z-index: 1;
  box-shadow: 0 0 0 2.5px var(--surface-subtle), 0 2px 6px rgba(28, 25, 21, 0.07);
  color: var(--ink);
}
.medal-ag img {
  width: 34px; height: 34px;
  object-fit: cover; display: block;
  transform: scale(1.16);
}
.medal-gpt svg { width: 22px; height: 22px; display: block; }
.brand-copy { display: flex; flex-direction: column; min-width: 0; line-height: 1.24; }
.brand-title {
  font-family: var(--serif); font-size: 15.5px; font-weight: 500;
  letter-spacing: -0.01em; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.brand-x { color: var(--ink-3); font-family: var(--sans); font-size: 12px; margin: 0 3px; font-weight: 400; }
.brand-sub { font-size: 11px; color: var(--ink-3); margin-top: 2px; letter-spacing: 0.02em; }

.side-head { padding: 4px 12px 10px; }
.search-field {
  position: relative; display: flex; align-items: center;
}
.search-icon {
  position: absolute; left: 10px; width: 13px; height: 13px; color: var(--ink-3); pointer-events: none;
}
.ctrl-input {
  width: 100%;
  background: var(--surface-warm); color: var(--ink);
  border: 1px solid transparent; border-radius: 9px;
  padding: 6px 10px 6px 30px; font-size: 12.5px; font-family: inherit;
  transition: background .15s, border-color .15s, box-shadow .15s;
}
.ctrl-input::placeholder { color: var(--ink-3); }
.ctrl-input:focus {
  outline: none; background: var(--surface);
  border-color: var(--border); box-shadow: var(--shadow-sm);
}
.ctrl-select {
  width: 100%;
  background: var(--surface-warm); color: var(--ink);
  border: 1px solid transparent; border-radius: 9px;
  padding: 6px 10px; font-size: 12.5px; font-family: inherit;
}
.ctrl-select:focus { outline: none; background: var(--surface); border-color: var(--border); }

.side-list { flex: 1; overflow-y: auto; padding: 2px 8px 14px; display: flex; flex-direction: column; gap: 6px; }
.proj-group { display: flex; flex-direction: column; }
.proj-head {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 8px; border-radius: 8px;
  cursor: pointer; list-style: none; user-select: none;
  color: var(--ink-2); font-size: 12px; font-weight: 600;
  transition: background .14s, color .14s;
}
.proj-head::-webkit-details-marker { display: none; }
.proj-head:hover { background: var(--surface-warm); color: var(--ink); }
.proj-chevron { width: 12px; height: 12px; color: var(--ink-3); flex-shrink: 0; transition: transform .15s ease; }
.proj-group[open] > .proj-head .proj-chevron { transform: rotate(90deg); }
.proj-folder { width: 13px; height: 13px; color: var(--clay); opacity: 0.85; flex-shrink: 0; }
.proj-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.proj-count {
  font-size: 10.5px; font-family: var(--mono); color: var(--ink-3);
  padding: 0 5px; border-radius: 999px; background: var(--surface-warm);
}
.proj-items {
  display: flex; flex-direction: column; gap: 2px;
  padding: 3px 0 2px 12px; margin-left: 11px;
  border-left: 1px solid var(--border-soft);
}
.side-item {
  width: 100%; text-align: left; cursor: pointer;
  padding: 7px 9px; border-radius: 9px;
  border: 1px solid transparent; background: transparent; color: var(--ink);
  transition: background .14s;
}
.side-item:hover { background: var(--surface-warm); }
.side-item.active { background: var(--surface); border-color: var(--border-soft); box-shadow: var(--shadow-sm); }
.side-item-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.side-title { font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.status-dot.running { background: var(--clay); animation: pulse 1.3s ease-in-out infinite; }
.status-dot.done { background: var(--sage); opacity: 0.55; }
.side-meta {
  display: flex; align-items: center; gap: 6px;
  font-size: 10.5px; color: var(--ink-3); margin-top: 3px;
  min-width: 0;
}
.side-meta-txt { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.dir-icons {
  display: inline-flex; align-items: center; gap: 2.5px;
  padding: 2px 5px; border-radius: 6px;
  background: var(--surface-warm); color: var(--ink-2);
  flex-shrink: 0;
}
.mini-icon { width: 11px; height: 11px; display: block; flex-shrink: 0; }
.mini-arrow { width: 9px; height: 9px; color: var(--ink-3); display: block; flex-shrink: 0; }
.empty-side { padding: 24px 10px; text-align: center; font-size: 12px; color: var(--ink-3); }

/* Single calm editorial header on chat pane */
.chat-pane { display: flex; flex-direction: column; overflow: hidden; min-width: 0; background: var(--canvas); }
.chat-header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 11px 20px;
  background: color-mix(in srgb, var(--canvas) 88%, transparent);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
}
.thread-meta { min-width: 0; flex: 1; }
.thread-title-group { min-width: 0; }
.thread-title-row { display: flex; align-items: center; gap: 8px; }
.thread-name {
  font-family: var(--serif); font-size: 15.5px; font-weight: 500;
  color: var(--ink); letter-spacing: 0.01em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.header-run-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--clay);
  animation: pulse 1.2s ease-in-out infinite; flex-shrink: 0;
}
.thread-sub {
  font-size: 11.5px; color: var(--ink-3); margin-top: 1px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.header-controls { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.live-pill {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 8px; border-radius: 999px;
  font-size: 11px; color: var(--ink-3); background: transparent;
}
.live-pill.offline { color: var(--amber); }
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--sage); }
.live-pill.offline .live-dot { background: var(--amber); }

/* Sleek Claude-style toggle pill instead of raw checkbox */
.follow-toggle {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 4px 10px 4px 7px; border-radius: 999px;
  background: transparent; border: 1px solid transparent;
  color: var(--ink-2); font-size: 12px; font-family: inherit;
  cursor: pointer; transition: background .15s, color .15s;
}
.follow-toggle:hover { background: var(--surface-warm); color: var(--ink); }
.toggle-track {
  width: 24px; height: 14px; border-radius: 999px;
  background: var(--border); position: relative; transition: background .18s;
  flex-shrink: 0;
}
.toggle-thumb {
  position: absolute; top: 2px; left: 2px;
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--surface); box-shadow: 0 1px 2px rgba(0,0,0,0.18);
  transition: transform .18s;
}
.follow-toggle.is-on .toggle-track { background: var(--clay); }
.follow-toggle.is-on .toggle-thumb { transform: translateX(10px); }
.follow-toggle.is-on { color: var(--ink); }

.icon-btn {
  width: 28px; height: 28px; border-radius: 8px;
  border: 1px solid transparent; background: transparent;
  color: var(--ink-2); display: grid; place-items: center;
  cursor: pointer; transition: background .15s, color .15s;
}
.icon-btn:hover { background: var(--surface-warm); color: var(--ink); }
.icon-btn svg { width: 14px; height: 14px; transition: transform .5s ease; }
.icon-btn.spinning svg { transform: rotate(360deg); }

.chat-scroll { flex: 1; overflow-y: auto; padding: 18px 16px 36px; }
.chat-feed { max-width: 740px; margin: 0 auto; display: flex; flex-direction: column; gap: 24px; }
.round-pair { display: flex; flex-direction: column; gap: 16px; }

.msg-row { display: flex; width: 100%; }
.msg-row.from-right, .msg-row.from-ag { justify-content: flex-end; }
.msg-row.from-left, .msg-row.from-codex { justify-content: flex-start; }

.msg-col { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.ag-col { max-width: 86%; align-items: flex-end; }
.left-dispatch-col { max-width: 86%; align-items: flex-start; }
.codex-col { width: 100%; max-width: 94%; align-items: flex-start; }
.exec-right-col { width: 100%; max-width: 94%; align-items: flex-end; }

.speaker-line { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--ink-3); }
.speaker-line.right { justify-content: flex-end; }
.avatar {
  width: 32px; height: 32px; border-radius: 999px;
  display: grid; place-items: center;
  flex-shrink: 0; border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}
.avatar-svg { width: 20px; height: 20px; display: block; }
.avatar-ag { background: var(--surface); }
.avatar-cx { background: var(--surface); color: var(--ink); }
.speaker-name { font-weight: 600; color: var(--ink-2); }
.round-tag {
  padding: 1px 7px; border-radius: 999px; font-size: 10.5px;
  background: var(--ag-soft); color: var(--ag-accent); font-weight: 500;
}
.model-tag { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
.speaker-time { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); }
.live-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 1px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 600;
  background: var(--clay-soft); color: var(--clay);
}
.pulse-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.3; transform: scale(1.4); } }

/* Dispatcher bubble (left for Codex, right for Antigravity) */
.bubble-dispatch {
  background: var(--bubble-ag);
  border: 1px solid var(--bubble-ag-border);
  padding: 11px 15px;
  color: var(--ink);
  width: 100%;
  box-shadow: var(--shadow-sm);
}
.bubble-right { border-radius: 16px 4px 16px 16px; }
.bubble-left { border-radius: 4px 16px 16px 16px; margin-left: 40px; width: calc(100% - 40px); }
.more-prompt { margin-top: 8px; border-top: 1px solid var(--border); padding-top: 7px; }
.more-prompt summary {
  cursor: pointer; font-size: 11.5px; color: var(--ag-accent); font-weight: 500;
  list-style: none; user-select: none;
}
.more-prompt summary::-webkit-details-marker { display: none; }
.prompt-full {
  margin: 8px 0 0; padding: 10px 12px; border-radius: 9px;
  background: var(--surface); border: 1px solid var(--border);
  font-family: var(--mono); font-size: 11.5px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-word; color: var(--ink-2);
  max-height: 300px; overflow-y: auto;
}

/* Executor editorial column (left for Codex, right for Antigravity) */
.exec-content { width: 100%; display: flex; flex-direction: column; gap: 9px; }
.exec-align-left { padding-left: 40px; }
.exec-align-right { padding-right: 40px; }

/* Claude-style collapsible thinking/work drawer */
.trace-drawer {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-subtle);
  overflow: hidden;
  transition: border-color .2s;
}
.trace-drawer.is-running { border-color: var(--clay); background: var(--surface); }
.trace-summary {
  padding: 8px 12px; cursor: pointer; list-style: none;
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--ink-2); user-select: none;
}
.trace-summary::-webkit-details-marker { display: none; }
.trace-summary:hover { color: var(--ink); }
.trace-icon { color: var(--clay); display: inline-flex; align-items: center; font-size: 12px; }
.trace-label { flex: 1; font-weight: 500; }
.trace-chevron { font-size: 11px; color: var(--ink-3); transition: transform .15s; }
.trace-drawer[open] .trace-chevron { transform: rotate(180deg); }

.trace-body {
  padding: 6px 12px 11px;
  border-top: 1px solid var(--border-soft);
  display: flex; flex-direction: column; gap: 7px;
}
.step { display: flex; flex-direction: column; }
.step-note {
  flex-direction: row; align-items: flex-start; gap: 7px;
  padding: 6px 9px; border-radius: 8px; background: var(--surface);
  border: 1px solid var(--border-soft); font-size: 12px; color: var(--ink-2);
}
.step-icon { color: var(--ink-3); flex-shrink: 0; font-size: 12px; }
.step-text { flex: 1; min-width: 0; }
.step-wait { font-size: 12px; color: var(--ink-3); padding: 4px 2px; }

.step-box {
  border: 1px solid var(--border-soft); border-radius: 8px;
  background: var(--surface); overflow: hidden;
}
.step-box summary {
  padding: 6px 10px; cursor: pointer; list-style: none;
  display: flex; align-items: center; gap: 7px; font-size: 11.5px;
}
.step-box summary::-webkit-details-marker { display: none; }
.cmd-preview, .file-name {
  flex: 1; font-family: var(--mono); font-size: 11.5px; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.step-pill {
  padding: 1px 6px; border-radius: 999px; font-size: 10.5px; font-family: var(--mono); flex-shrink: 0;
}
.st-ok { background: var(--sage-soft); color: var(--sage); }
.st-err { background: var(--crimson-soft); color: var(--crimson); }
.st-run { background: var(--amber-soft); color: var(--amber); }

.step-out {
  margin: 0; padding: 8px 10px; border-top: 1px solid var(--border-soft);
  background: var(--code-bg); font-family: var(--mono); font-size: 11px;
  white-space: pre-wrap; word-break: break-word; color: var(--ink-2);
  max-height: 220px; overflow-y: auto;
}

.file-kind {
  padding: 1px 6px; border-radius: 5px; font-size: 10.5px; font-weight: 600; flex-shrink: 0;
}
.k-add { background: var(--sage-soft); color: var(--sage); }
.k-upd { background: var(--ag-soft); color: var(--ag-accent); }
.k-del { background: var(--crimson-soft); color: var(--crimson); }
.diff-hint { font-size: 10.5px; color: var(--ink-3); flex-shrink: 0; }

.diff-view {
  padding: 6px 0; border-top: 1px solid var(--border-soft);
  background: var(--code-bg); font-family: var(--mono); font-size: 11px;
  max-height: 240px; overflow: auto;
}
.diff-line { padding: 0 10px; white-space: pre; }
.diff-add { background: var(--sage-soft); color: var(--sage); }
.diff-del { background: var(--crimson-soft); color: var(--crimson); }
.diff-hunk { color: var(--ag-accent); font-weight: 600; }

/* Executor formal reply card */
.reply-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px 16px 16px 16px;
  padding: 12px 16px;
  box-shadow: var(--shadow-sm);
}
.reply-right { border-radius: 16px 4px 16px 16px; }

.rich-body {
  font-family: var(--serif);
  font-size: 14.5px;
  line-height: 1.76;
  letter-spacing: 0.01em;
}
.rich-body p { margin: 0 0 8px; }
.rich-body p:last-child { margin-bottom: 0; }
.rich-list { margin: 5px 0 9px; padding-left: 20px; }
.rich-list:last-child { margin-bottom: 0; }
.rich-list li { margin: 4px 0; }
.rich-list li.nested-li { margin-left: 16px; list-style-type: circle; font-size: 0.95em; color: var(--ink-2); }
.bubble-ag .rich-body { font-size: 14px; line-height: 1.7; }
.step-note .rich-body { font-size: 12.5px; line-height: 1.6; }
.live-whisper { font-family: var(--serif); }
.inline-code {
  font-family: var(--mono); font-size: 11.5px;
  padding: 1.5px 5.5px; border-radius: 5px;
  background: var(--code-bg); border: 1px solid var(--border-soft);
  color: var(--ink);
  overflow-wrap: anywhere; word-break: break-word;
}
.md-h { font-family: var(--serif); margin: 10px 0 6px; color: var(--ink); font-weight: 600; }
.md-h1 { font-size: 16px; }
.md-h2 { font-size: 15px; }
.md-h3, .md-h4 { font-size: 14px; }
.md-hr { border: 0; border-top: 1px solid var(--border); margin: 10px 0; }
.md-quote {
  margin: 8px 0; padding: 6px 12px;
  border-left: 3px solid var(--clay);
  background: var(--surface-subtle); color: var(--ink-2);
  border-radius: 0 8px 8px 0;
}
.md-code {
  margin: 8px 0; padding: 10px 12px; border-radius: 9px;
  background: var(--code-bg); border: 1px solid var(--border);
  font-family: var(--mono); font-size: 11.5px; line-height: 1.55;
  overflow-x: auto; color: var(--ink);
}
.table-wrap {
  margin: 10px 0; border-radius: 10px;
  border: 1px solid var(--border);
  overflow-x: auto; background: var(--surface);
}
.md-table {
  width: 100%; border-collapse: collapse;
  font-family: var(--sans); font-size: 12.5px; line-height: 1.58;
}
.md-table th {
  text-align: left; font-weight: 600; color: var(--ink-2);
  background: var(--surface-subtle);
  padding: 8px 11px; border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.md-table td {
  padding: 8px 11px; border-bottom: 1px solid var(--border-soft);
  vertical-align: top; color: var(--ink);
  overflow-wrap: anywhere; word-break: break-word;
}
.md-table tr:last-child td { border-bottom: 0; }
.md-table tbody tr:hover { background: var(--surface-subtle); }

.touched-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--border-soft); }
.touched-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 9px; border-radius: 8px;
  background: var(--surface-subtle); border: 1px solid var(--border);
  color: var(--ink-2); font-family: var(--mono); font-size: 11px;
}
.chip-mark { font-weight: 700; padding: 0 3px; border-radius: 4px; }

.live-whisper {
  font-size: 12px; color: var(--ink-2); font-style: italic; padding-left: 2px;
}
.thinking-line {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--ink-3); padding: 4px 2px;
}
.typing-dots { display: inline-flex; gap: 3px; }
.typing-dots span {
  width: 5px; height: 5px; border-radius: 50%; background: var(--clay);
  animation: bounce 1.1s infinite ease-in-out;
}
.typing-dots span:nth-child(2) { animation-delay: 0.16s; }
.typing-dots span:nth-child(3) { animation-delay: 0.32s; }
@keyframes bounce { 0%,80%,100% { opacity: 0.25; transform: scale(0.8); } 40% { opacity: 1; transform: scale(1.1); } }

.spinner {
  width: 11px; height: 11px; border-radius: 50%;
  border: 1.8px solid currentColor; border-top-color: transparent;
  display: inline-block; animation: spin .75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.empty-chat { text-align: center; padding: 64px 20px; color: var(--ink-2); }
.empty-emblem { font-size: 26px; color: var(--clay); margin-bottom: 10px; }
.empty-title { font-family: var(--serif); font-size: 16px; color: var(--ink); font-weight: 600; }
.empty-sub { font-size: 12.5px; color: var(--ink-3); max-width: 340px; margin: 6px auto 0; }
.mono { font-family: var(--mono); }
.muted { color: var(--ink-3); }
`;

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Codex Monitor</title>
<style>${css}</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <clipPath id="gemStarClip">
      <path d="M12 1.2C12 7.18 7.18 12 1.2 12C7.18 12 12 16.82 12 22.8C12 16.82 16.82 12 22.8 12C16.82 12 12 7.18 12 1.2Z"/>
    </clipPath>
    <radialGradient id="gemRed" cx="50%" cy="50%" r="55%">
      <stop offset="0%" stop-color="#EA4335"/>
      <stop offset="58%" stop-color="#EA4335" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#EA4335" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="gemYellow" cx="50%" cy="50%" r="55%">
      <stop offset="0%" stop-color="#FBBC04"/>
      <stop offset="56%" stop-color="#FBBC04" stop-opacity="0.92"/>
      <stop offset="100%" stop-color="#FBBC04" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="gemGreen" cx="50%" cy="50%" r="55%">
      <stop offset="0%" stop-color="#34A853"/>
      <stop offset="54%" stop-color="#34A853" stop-opacity="0.88"/>
      <stop offset="100%" stop-color="#34A853" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="gemBlue" cx="50%" cy="50%" r="58%">
      <stop offset="0%" stop-color="#4285F4"/>
      <stop offset="60%" stop-color="#4285F4" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="#4285F4" stop-opacity="0"/>
    </radialGradient>
  </defs>
</svg>
<div class="shell">
  <aside class="sidebar">
    <div class="side-brand">
      <div class="brand-medallions" aria-hidden="true">
        <span class="medal medal-ag">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="15" fill="#F4F1EA"/><path d="M21 43 43 21" fill="none" stroke="#3D6470" stroke-width="5" stroke-linecap="round"/><circle cx="20" cy="44" r="9" fill="#3D6470"/><circle cx="44" cy="20" r="9" fill="#C96442"/><circle cx="20" cy="44" r="3" fill="#F4F1EA"/><circle cx="44" cy="20" r="3" fill="#F4F1EA"/></svg>
        </span>
        <span class="medal medal-gpt">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/>
          </svg>
        </span>
      </div>
      <div class="brand-copy">
        <span class="brand-title">Antigravity<span class="brand-x">×</span>Codex</span>
        <span class="brand-sub">单向实时监控</span>
      </div>
    </div>
    <div class="side-head">
      <div class="search-field">
        <svg class="search-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="9" cy="9" r="5.5"/><path d="M13.5 13.5L17 17" stroke-linecap="round"/></svg>
        <input id="side-search" class="ctrl-input" type="search" placeholder="搜索对话..." />
      </div>
    </div>
    <div id="side-list" class="side-list"></div>
  </aside>

  <section class="chat-pane">
    <header class="chat-header">
      <div id="thread-meta" class="thread-meta"></div>
      <div class="header-controls">
        <span id="live-pill" class="live-pill" title="每 0.8 秒自动同步"><span class="live-dot"></span><span id="live-text">实时同步</span></span>
        <button id="follow-btn" class="follow-toggle is-on" type="button" aria-pressed="true" title="新派单出现时自动切换并滚动到底部">
          <span class="toggle-track"><span class="toggle-thumb"></span></span>
          <span>自动跟随</span>
        </button>
        <button id="theme-btn" class="icon-btn" type="button" title="切换浅色 / 深色主题">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.2 12.1A6.5 6.5 0 0 1 7.9 3.8a6.5 6.5 0 1 0 8.3 8.3z"/></svg>
        </button>
        <button id="refresh-btn" class="icon-btn" type="button" title="立即刷新">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.5 10a6.5 6.5 0 1 1-1.6-4.25"/><path d="M16.5 3.5v3.2h-3.2"/></svg>
        </button>
      </div>
    </header>

    <div class="compact-switcher">
      <select id="sess-select" class="ctrl-select"></select>
    </div>

    <div id="chat-scroll" class="chat-scroll">
      <div id="chat-feed" class="chat-feed"></div>
    </div>
  </section>
</div>
<script>${js}</script>
</body>
</html>`;

const outPath = path.join(outDir, 'app.html');
fs.writeFileSync(outPath, html, 'utf8');
console.log(`Built ${outPath} (${Buffer.byteLength(html, 'utf8')} bytes)`);
