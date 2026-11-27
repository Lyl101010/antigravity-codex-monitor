import hashlib
import json
import os
import pathlib
import sys
import threading
import time

import dashboard_server as dashboard_server_loader
from mcp.types import CallToolResult, TextContent

try:
    from mcp.server import MCPServer as _MCPServer
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as _MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP as _MCPServer

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent
APP_HTML_PATH = PLUGIN_ROOT / "ui" / "app.html"
APP_URI = "ui://antigravity-codex-monitor/app.html"
HTTP_PORT = 18423
MAX_PAYLOAD_BYTES = 750_000


def _start_embedded_http_server() -> None:
    """Keep http://127.0.0.1:18423 alive for Codex Desktop Web Preview as long as this MCP server runs."""
    try:
        server = dashboard_server_loader.ThreadingHTTPServer(
            ("127.0.0.1", HTTP_PORT), dashboard_server_loader.DashboardHandler
        )
        t = threading.Thread(target=server.serve_forever, daemon=True, name="codex-monitor-http")
        t.start()
    except OSError:
        # Port already served by another instance (e.g. Codex or Antigravity MCP server)
        pass

PANE_META = {
    "ui": {"resourceUri": APP_URI},
    "com.google.antigravity/ui": {
        "entrypoints": [
            {"type": "aux_pane", "title": "antigravity-codex-monitor"},
            {"type": "global", "title": "antigravity-codex-monitor"},
        ]
    },
}
APP_ONLY_META = {"ui": {"visibility": ["app"]}}

mcp = _MCPServer("antigravity-codex-monitor")


def _fingerprint(limit, session_id, dispatch_id):
    args = (session_id, dispatch_id) if session_id or dispatch_id else ()
    files = dashboard_server_loader.compute_fingerprint(*args)
    return hashlib.sha256(json.dumps([limit, session_id, dispatch_id, files]).encode("ascii")).hexdigest()


def _dispatch_matches(session, dispatch_id):
    return bool(dispatch_id and (
        session.get("session_id") == dispatch_id or dispatch_id in session.get("dispatch_ids", [])
        or any(t.get("dispatch_id") == dispatch_id for t in session.get("turns", []))
    ))


def _session_priority(session, session_id, dispatch_id):
    if _dispatch_matches(session, dispatch_id):
        return 3
    if session_id and session.get("session_id") == session_id:
        return 2
    return int(session.get("status") == "running")


def _payload_size(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _fit_state(state, max_bytes, session_id="", dispatch_id=""):
    try:
        size = _payload_size(state)
    except UnicodeEncodeError:
        state = json.loads(json.dumps(state, ensure_ascii=False).encode("utf-8", errors="replace"))
        size = _payload_size(state)
    if size <= max_bytes:
        return state

    sessions = state.get("sessions", [])
    minimum = _payload_size({**state, "sessions": []})
    if minimum > max_bytes:
        raise ValueError("Dashboard payload exceeds its byte budget")
    for sess in sessions:
        for turn in sess.get("turns", []):
            for ev in turn.get("events", []):
                if ev.get("kind") == "command" and len(ev.get("output", "")) > 800:
                    ev["output"] = ev["output"][:800] + "\n... (trimmed)"
                elif ev.get("kind") == "file_change":
                    for ch in ev.get("changes", []):
                        if len(ch.get("diff", "")) > 2000:
                            ch["diff"] = ch["diff"][:2000] + "\n... (trimmed)"
    sizes = [_payload_size(s) + 2 for s in sessions]
    size = minimum + sum(sizes) - 2
    kept = set(range(len(sessions)))
    for index in sorted(kept, key=lambda i: (_session_priority(sessions[i], session_id, dispatch_id), -i)):
        if size <= max_bytes or len(kept) == 1:
            break
        kept.remove(index)
        size -= sizes[index]
    state["sessions"] = [s for i, s in enumerate(sessions) if i in kept]
    if _payload_size(state) <= max_bytes:
        return state

    session = state["sessions"][0]
    turns = session.get("turns", [])
    for turn in turns:
        for key in ("prompt", "final_message"):
            if key in turn:
                turn[key] = turn[key][:4000]
        turn["events"] = []
        if "files" in turn:
            turn["files"] = []
    if _payload_size(state) <= max_bytes:
        return state

    if turns:
        target_turn = next((t for t in turns if dispatch_id and t.get("dispatch_id") == dispatch_id), turns[-1])
        session["turns"] = [target_turn]
        if _payload_size(state) <= max_bytes:
            return state
        for key in ("prompt", "final_message", "cwd", "model", "effort", "dispatcher_model", "started_fmt", "ended_fmt"):
            if key in target_turn:
                target_turn[key] = ""
    for key in ("cwd", "project", "model", "effort", "updated_fmt", "dispatcher_model"):
        if key in session:
            session[key] = ""
    if _payload_size(state) <= max_bytes:
        return state
    raise ValueError("Dashboard session exceeds its byte budget")


def _compact_state(limit: int = 15, max_bytes: int | None = None, *, session_id="", dispatch_id="",
                   fingerprint="", opened_at_ms=None) -> dict:
    args = {"include_session_id": session_id, "include_dispatch_id": dispatch_id} if session_id or dispatch_id else {}
    state = dashboard_server_loader.build_state(limit=max(0, min(limit, 200)), **args)
    state.update(fingerprint=fingerprint or _fingerprint(limit, session_id, dispatch_id), changed=True)
    if opened_at_ms is not None:
        state["opened_at_ms"] = opened_at_ms
        if session_id:
            state["focus_session_id"] = session_id
        if dispatch_id:
            state["focus_dispatch_id"] = dispatch_id
    return _fit_state(state, MAX_PAYLOAD_BYTES if max_bytes is None else max_bytes, session_id, dispatch_id)


def _state_error(exc):
    message = f"Could not load Codex state ({type(exc).__name__})."
    if isinstance(exc, ValueError) and "byte budget" in str(exc):
        message = str(exc)
    return CallToolResult(content=[TextContent(type="text", text=message)],
                          structuredContent={"error": message}, isError=True)


@mcp.tool(
    name="open_codex_monitor",
    title="antigravity-codex-monitor",
    description="Open or refresh the live Codex Mission Control panel in Antigravity's side pane to watch Codex CLI (codex exec) tasks in real time.",
    meta=PANE_META,
)
def open_codex_monitor(session_id: str = "", dispatch_id: str = "") -> CallToolResult:
    try:
        state = _compact_state(session_id=session_id, dispatch_id=dispatch_id, opened_at_ms=int(time.time() * 1000))
    except Exception as exc:
        return _state_error(exc)
    sessions = state.get("sessions", [])
    running = sum(1 for s in sessions if s.get("status") == "running")
    latest_model = sessions[0].get("model", "-") if sessions else "-"
    summary = f"Codex Monitor ready ({len(sessions)} session(s), {running} running, latest model: {latest_model})."
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structuredContent=state,
    )


@mcp.tool(
    name="get_codex_state",
    title="Poll Codex State",
    description="App-only live polling tool returning updated Codex exec sessions when fingerprint changes.",
    meta=APP_ONLY_META,
)
def get_codex_state(last_fingerprint: str = "", limit: int = 15, session_id: str = "", dispatch_id: str = "") -> CallToolResult:
    try:
        fp = _fingerprint(limit, session_id, dispatch_id)
        if fp == last_fingerprint:
            payload = {"changed": False, "fingerprint": fp, "server_time_ms": int(time.time() * 1000)}
            if _payload_size(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("Dashboard payload exceeds its byte budget")
            return CallToolResult(content=[TextContent(type="text", text="unchanged")], structuredContent=payload)
        state = _compact_state(limit=limit, session_id=session_id, dispatch_id=dispatch_id, fingerprint=fp)
    except Exception as exc:
        return _state_error(exc)
    return CallToolResult(
        content=[TextContent(type="text", text="updated")],
        structuredContent=state,
    )


@mcp.resource(
    APP_URI,
    name="Codex Monitor UI",
    mime_type="text/html;profile=mcp-app",
    meta={"ui": {"prefersBorder": False}},
)
def codex_monitor_html() -> str:
    return APP_HTML_PATH.read_text(encoding="utf-8")


if __name__ == "__main__":
    if "--enable-http" in sys.argv or os.environ.get("CODEX_MONITOR_ENABLE_HTTP") == "1":
        _start_embedded_http_server()
    mcp.run()
