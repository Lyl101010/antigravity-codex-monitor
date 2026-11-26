import argparse
import json
import os
import pathlib
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse


CODEX_HOME = pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex").expanduser()
STATE_DB = max(CODEX_HOME.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, default=CODEX_HOME / "state_missing.sqlite")
SESSIONS_DIR = CODEX_HOME / "sessions"
DISPATCH_LOG = CODEX_HOME / "codex-worker-dispatches.json"
DEFAULT_PORT = 18423

_CLIENTS_LOCK = threading.Lock()
_ACTIVE_CLIENTS = 0
_SERVER_REF = None


def clean_path(p):
    if not p:
        return ""
    s = str(p)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return s


def path_key(p):
    value = os.path.normpath(clean_path(p)) if p else ""
    return value.lower() if os.name == "nt" else value


def parse_dt(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            if value > 1e12:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_ts(value):
    dt = parse_dt(value)
    if not dt:
        return "-"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def to_epoch_ms(value):
    dt = parse_dt(value)
    return int(dt.timestamp() * 1000) if dt else None


def strip_discipline_footer(text):
    marker = "[EXECUTION DISCIPLINE]"
    idx = text.find(marker)
    return text[:idx].strip() if idx != -1 else text.strip()


def read_session_header(path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            line = f.readline()
        if not line:
            return None
        obj = json.loads(line)
        if obj.get("type") != "session_meta":
            return None
        pl = obj.get("payload") or {}
        return {
            "session_id": pl.get("id") or pl.get("session_id") or "",
            "source": pl.get("source") or "",
            "cwd": clean_path(pl.get("cwd") or ""),
            "created_at": obj.get("timestamp"),
        }
    except Exception:
        return None


def collect_exec_threads(limit=30):
    by_sid = {}

    if STATE_DB.is_file():
        try:
            con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
            rows = con.execute(
                """
                SELECT id, rollout_path, cwd, created_at, updated_at, model, reasoning_effort, tokens_used
                FROM threads
                WHERE source = 'exec'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            con.close()
            for r in rows:
                rp = clean_path(r[1])
                mtime = pathlib.Path(rp).stat().st_mtime if rp and pathlib.Path(rp).is_file() else (r[4] or 0)
                by_sid[r[0]] = {
                    "session_id": r[0],
                    "rollout_path": rp,
                    "cwd": clean_path(r[2]),
                    "created_at": r[3],
                    "updated_at": mtime,
                    "model": r[5] or "",
                    "effort": r[6] or "",
                    "tokens_used": r[7] or 0,
                }
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc) and "no such column" not in str(exc):
                raise

    # Scan recent sessions alongside the database; scan all dates when it is absent.
    now = datetime.now()
    if STATE_DB.is_file():
        day_dirs = [SESSIONS_DIR / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" for day in (now - timedelta(days=delta) for delta in range(3))]
    else:
        day_dirs = [p for p in SESSIONS_DIR.glob("*/*/*") if p.is_dir()]
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        for fp in day_dir.glob("rollout-*.jsonl"):
            try:
                st = fp.stat()
            except OSError:
                continue
            hdr = read_session_header(fp)
            if not hdr or hdr.get("source") != "exec":
                continue
            sid = hdr["session_id"]
            if not sid:
                continue
            if sid in by_sid:
                by_sid[sid]["updated_at"] = max(by_sid[sid]["updated_at"], st.st_mtime)
                if not by_sid[sid]["rollout_path"]:
                    by_sid[sid]["rollout_path"] = str(fp)
            else:
                by_sid[sid] = {
                    "session_id": sid,
                    "rollout_path": str(fp),
                    "cwd": hdr["cwd"],
                    "created_at": hdr["created_at"],
                    "updated_at": st.st_mtime,
                    "model": "",
                    "effort": "",
                    "tokens_used": 0,
                }

    ordered = sorted(by_sid.values(), key=lambda x: float(x.get("updated_at") or 0), reverse=True)
    return ordered[:limit]


def load_dispatches():
    if not DISPATCH_LOG.is_file():
        return []
    try:
        data = json.loads(DISPATCH_LOG.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def format_cmd(item):
    parsed = item.get("parsed_cmd")
    if isinstance(parsed, list) and parsed:
        parts = [p.get("cmd", "") for p in parsed if isinstance(p, dict) and p.get("cmd")]
        if parts:
            return " && ".join(parts)
    cmd = item.get("command")
    if isinstance(cmd, list):
        if len(cmd) >= 3 and cmd[1] in ("-Command", "-c"):
            return str(cmd[2])
        return " ".join(str(x) for x in cmd)
    return str(cmd or "")


def parse_rollout(path_str):
    p = pathlib.Path(path_str)
    if not p.is_file():
        return []

    turns = []
    current = None
    last_model = ""
    last_effort = ""
    last_cwd = ""

    def ensure_turn(turn_id, ts):
        nonlocal current
        if current is None or (turn_id and current.get("turn_id") != turn_id):
            current = {
                "turn_id": turn_id or f"turn-{len(turns) + 1}",
                "started_at": ts,
                "ended_at": None,
                "status": "running",
                "model": last_model,
                "effort": last_effort,
                "cwd": last_cwd,
                "prompt": "",
                "final_message": "",
                "events": [],
                "files": [],
                "cmd_count": 0,
                "tokens": None,
            }
            turns.append(current)
        return current

    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for raw in lines:
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        t = obj.get("type")
        ts = obj.get("timestamp")
        pl = obj.get("payload") or {}

        if t == "turn_context":
            last_model = pl.get("model") or last_model
            last_effort = pl.get("effort") or last_effort
            last_cwd = clean_path(pl.get("cwd") or last_cwd)
            tr = ensure_turn(pl.get("turn_id"), ts)
            tr["model"] = last_model
            tr["effort"] = last_effort
            tr["cwd"] = last_cwd

        elif t == "event_msg":
            sub = pl.get("type")
            if sub == "task_started":
                tid = (pl.get("turn_attribution") or {}).get("turn_id") or pl.get("turn_id")
                tr = ensure_turn(tid, ts)
                tr["started_at"] = ts
            elif sub == "thread_settings_applied":
                ts_obj = pl.get("thread_settings") or {}
                last_model = ts_obj.get("model") or last_model
                if current:
                    current["model"] = last_model
            elif sub == "item_completed":
                tr = ensure_turn(pl.get("turn_id"), ts)
                item = pl.get("item") or {}
                itype = item.get("type")
                if itype == "UserMessage":
                    texts = [c.get("text", "") for c in (item.get("content") or []) if isinstance(c, dict)]
                    full = "\n".join(x for x in texts if x).strip()
                    if full and not tr["prompt"]:
                        tr["prompt"] = strip_discipline_footer(full)
                elif itype == "AgentMessage":
                    texts = [c.get("text", "") or c.get("Text", "") for c in (item.get("content") or []) if isinstance(c, dict)]
                    msg = "\n".join(x for x in texts if x).strip()
                    if msg:
                        if item.get("phase") == "commentary":
                            tr["events"].append({
                                "kind": "commentary",
                                "ts": fmt_ts(ts),
                                "text": msg,
                            })
                        else:
                            tr["final_message"] = msg
                elif itype == "CommandExecution":
                    cmd_str = format_cmd(item)
                    dur_obj = item.get("duration") or {}
                    dur_s = None
                    if isinstance(dur_obj, dict) and "secs" in dur_obj:
                        dur_s = round(dur_obj.get("secs", 0) + dur_obj.get("nanos", 0) / 1e9, 2)
                    out = str(item.get("aggregated_output") or "")
                    if len(out) > 4000:
                        out = out[:4000] + "\n... (truncated)"
                    tr["cmd_count"] += 1
                    tr["events"].append({
                        "kind": "command",
                        "ts": fmt_ts(ts),
                        "cmd": cmd_str,
                        "exit_code": item.get("exit_code"),
                        "duration_s": dur_s,
                        "output": out,
                    })
                elif itype == "FileChange":
                    changes = item.get("changes") or {}
                    file_items = []
                    for fpath, meta in changes.items():
                        cpath = clean_path(fpath)
                        kind = meta.get("type", "edit") if isinstance(meta, dict) else "edit"
                        diff = ""
                        if isinstance(meta, dict):
                            if meta.get("unified_diff"):
                                diff = str(meta["unified_diff"])
                            elif meta.get("content"):
                                raw_lines = str(meta["content"]).splitlines()
                                diff = "\n".join("+" + ln for ln in raw_lines[:180])
                                if len(raw_lines) > 180:
                                    diff += f"\n... (+{len(raw_lines) - 180} more lines)"
                        if len(diff) > 10000:
                            diff = diff[:10000] + "\n... (truncated)"
                        f_entry = {"path": cpath, "kind": kind, "diff": diff}
                        file_items.append(f_entry)
                        existing = next((x for x in tr["files"] if path_key(x["path"]) == path_key(cpath)), None)
                        if existing is None:
                            tr["files"].append({"path": cpath, "kind": kind})
                        elif kind == "delete":
                            existing["kind"] = "delete"
                        elif existing["kind"] != "add":
                            existing["kind"] = kind
                    if file_items:
                        tr["events"].append({
                            "kind": "file_change",
                            "ts": fmt_ts(ts),
                            "changes": file_items,
                        })
            elif sub == "token_count":
                info = pl.get("info") or {}
                usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
                if current and usage:
                    inp = usage.get("input_tokens", 0) or 0
                    cached = usage.get("cached_input_tokens", 0) or 0
                    out = usage.get("output_tokens", 0) or 0
                    reas = usage.get("reasoning_output_tokens", 0) or 0
                    tot = usage.get("total_tokens", inp + out) or 0
                    current["tokens"] = {
                        "input": inp,
                        "cached": cached,
                        "output": out,
                        "reasoning": reas,
                        "total": tot,
                    }
            elif sub == "task_complete":
                tr = ensure_turn(pl.get("turn_id"), ts)
                tr["ended_at"] = ts
                tr["status"] = "completed"
                if pl.get("last_agent_message"):
                    tr["final_message"] = pl["last_agent_message"]

    for tr in turns:
        s_dt = parse_dt(tr["started_at"])
        e_dt = parse_dt(tr["ended_at"])
        tr["duration_s"] = round((e_dt - s_dt).total_seconds(), 1) if (s_dt and e_dt) else None
        tr["started_ms"] = to_epoch_ms(tr["started_at"])
        tr["started_fmt"] = fmt_ts(tr["started_at"])
        tr["ended_fmt"] = fmt_ts(tr["ended_at"])
    return turns


def build_state(limit=25):
    threads = collect_exec_threads(limit=limit)
    dispatches = load_dispatches()

    sessions = []
    by_sid = {}
    for th in threads:
        turns = parse_rollout(th["rollout_path"])
        if not turns:
            continue
        # Match each turn to its recorded dispatcher_model from codex-worker-dispatches.json
        sid_dispatches = [
            d for d in dispatches
            if d.get("direction", "ag_to_codex") == "ag_to_codex" and d.get("dispatcher_model") and (
                d.get("session_id") == th["session_id"]
                or path_key(d.get("target_root", "")) == path_key(th["cwd"])
            )
        ]
        for tr in turns:
            t_ms = tr.get("started_ms") or 0
            best_dm = ""
            best_diff = 10**18
            for d in sid_dispatches:
                d_ms = to_epoch_ms(d.get("started_at")) or 0
                diff = abs(t_ms - d_ms)
                if d.get("session_id") == th["session_id"] and diff < 120_000 and diff < best_diff:
                    best_diff = diff
                    best_dm = d.get("dispatcher_model") or ""
            if not best_dm:
                same_sid = [d for d in sid_dispatches if d.get("session_id") == th["session_id"]]
                if same_sid:
                    best_dm = same_sid[-1].get("dispatcher_model") or ""
            tr["dispatcher_model"] = best_dm

        model = th["model"] or next((t["model"] for t in reversed(turns) if t["model"]), "unknown")
        effort = th["effort"] or next((t["effort"] for t in reversed(turns) if t["effort"]), "")
        item = {
            "session_id": th["session_id"],
            "direction": "ag_to_codex",
            "cwd": th["cwd"],
            "project": pathlib.Path(th["cwd"]).name if th["cwd"] else "unknown",
            "model": model,
            "effort": effort,
            "updated_fmt": fmt_ts(th["updated_at"]),
            "updated_ms": to_epoch_ms(th["updated_at"]),
            "tokens_used": th["tokens_used"],
            "status": turns[-1]["status"],
            "turns": turns,
        }
        sessions.append(item)
        by_sid[th["session_id"]] = item

    # Merge any active running ag_to_codex dispatch not yet reflected in a rollout file
    for d in reversed(dispatches):
        if d.get("direction", "ag_to_codex") != "ag_to_codex" or d.get("status") != "running":
            continue
        sid = d.get("session_id") or ""
        if sid and sid in by_sid:
            if by_sid[sid]["status"] != "running":
                by_sid[sid]["status"] = "running"
            continue
        d_ms = to_epoch_ms(d.get("started_at")) or 0
        d_root = path_key(d.get("target_root", ""))
        matched = False
        for s in sessions:
            if path_key(s["cwd"]) == d_root and s["turns"] and (s["turns"][-1].get("started_ms") or 0) >= d_ms - 5000:
                matched = True
                break
        if matched:
            continue
        cwd = clean_path(d.get("target_root", ""))
        sessions.insert(0, {
            "session_id": d.get("dispatch_id", "pending"),
            "direction": "ag_to_codex",
            "cwd": cwd,
            "project": pathlib.Path(cwd).name if cwd else "unknown",
            "model": d.get("model") or "config default",
            "effort": d.get("effort") or "medium",
            "updated_fmt": fmt_ts(d.get("started_at")),
            "updated_ms": d_ms,
            "tokens_used": 0,
            "status": "running",
            "turns": [{
                "turn_id": "pending",
                "started_ms": d_ms,
                "started_fmt": fmt_ts(d.get("started_at")),
                "ended_fmt": "-",
                "duration_s": None,
                "status": "running",
                "model": d.get("model") or "config default",
                "dispatcher_model": d.get("dispatcher_model") or "",
                "effort": d.get("effort") or "medium",
                "cwd": cwd,
                "prompt": strip_discipline_footer(d.get("prompt_preview", "")),
                "final_message": "",
                "events": [{"kind": "commentary", "ts": fmt_ts(d.get("started_at")), "text": "任务已派发，等待 Codex 启动并写入事件流..."}],
                "files": [],
                "cmd_count": 0,
                "tokens": None,
            }],
        })

    sessions.sort(key=lambda s: (s.get("status") == "running", int(s.get("updated_ms") or 0)), reverse=True)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "server_time_ms": int(time.time() * 1000),
        "sessions": sessions[:limit],
    }


def compute_fingerprint():
    parts = []
    if DISPATCH_LOG.is_file():
        try:
            st = DISPATCH_LOG.stat()
            parts.append(f"d:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            pass
    if STATE_DB.is_file():
        try:
            st = STATE_DB.stat()
            parts.append(f"db:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            pass
    now = datetime.now()
    if STATE_DB.is_file():
        day_dirs = [SESSIONS_DIR / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" for day in (now - timedelta(days=delta) for delta in range(2))]
    else:
        day_dirs = [p for p in SESSIONS_DIR.glob("*/*/*") if p.is_dir()]
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        for fp in sorted(day_dir.glob("rollout-*.jsonl")):
            try:
                st = fp.stat()
                parts.append(f"{fp.name}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                pass
    return "|".join(parts)



class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/state"):
            body = json.dumps(build_state(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path == "/":
            body = (pathlib.Path(__file__).parent / "ui" / "app.html").read_bytes()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler).serve_forever()
