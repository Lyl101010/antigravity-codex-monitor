import json
import pathlib
import sys
import threading
import time

import os
import dashboard_server as dashboard_server_loader
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent
APP_HTML_PATH = PLUGIN_ROOT / "ui" / "app.html"
APP_URI = "ui://antigravity-codex-monitor/app.html"
HTTP_PORT = 18423


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

mcp = FastMCP("antigravity-codex-monitor")


def _compact_state(limit: int = 15, max_bytes: int = 750_000) -> dict:
    state = dashboard_server_loader.build_state(limit=limit)
    state["fingerprint"] = dashboard_server_loader.compute_fingerprint()
    raw = json.dumps(state, ensure_ascii=False).encode("utf-8")
    if len(raw) <= max_bytes:
        return state
    # Trim long command outputs / diffs if payload grows near the 1 MiB trajectory cap
    for sess in state.get("sessions", []):
        for turn in sess.get("turns", []):
            for ev in turn.get("events", []):
                if ev.get("kind") == "command" and len(ev.get("output", "")) > 800:
                    ev["output"] = ev["output"][:800] + "\n... (trimmed)"
                elif ev.get("kind") == "file_change":
                    for ch in ev.get("changes", []):
                        if len(ch.get("diff", "")) > 2000:
                            ch["diff"] = ch["diff"][:2000] + "\n... (trimmed)"
    while len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > max_bytes and state.get("sessions"):
        state["sessions"].pop()
    return state


@mcp.tool(
    name="open_codex_monitor",
    title="antigravity-codex-monitor",
    description="Open or refresh the live Codex Mission Control panel in Antigravity's side pane to watch Codex CLI (codex exec) tasks in real time.",
    meta=PANE_META,
)
def open_codex_monitor(session_id: str = "") -> CallToolResult:
    state = _compact_state(limit=15)
    if session_id:
        state["focus_session_id"] = session_id
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
def get_codex_state(last_fingerprint: str = "", limit: int = 15) -> CallToolResult:
    fp = dashboard_server_loader.compute_fingerprint()
    if last_fingerprint and fp == last_fingerprint:
        payload = {"changed": False, "fingerprint": fp, "server_time_ms": int(time.time() * 1000)}
        return CallToolResult(
            content=[TextContent(type="text", text="unchanged")],
            structuredContent=payload,
        )
    state = _compact_state(limit=limit)
    state["changed"] = True
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
