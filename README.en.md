# Antigravity Codex Monitor

[简体中文](./README.md) · **English**

[![Version](https://img.shields.io/badge/version-1.1.1-3D6470.svg)](https://github.com/Lyl101010/antigravity-codex-monitor)
[![Antigravity](https://img.shields.io/badge/Antigravity-2.x%20MCP%20App-C96442.svg)](https://github.com/Lyl101010/antigravity-codex-monitor)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-4B5563.svg)](https://github.com/Lyl101010/antigravity-codex-monitor)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Antigravity Codex Monitor MCP server](https://glama.ai/mcp/servers/Lyl101010/antigravity-codex-monitor/badges/score.svg)](https://glama.ai/mcp/servers/Lyl101010/antigravity-codex-monitor)

A native **MCP App** side-pane Mission Control UI and cross-platform `codex-worker` delegation bundle for **Google Antigravity**.

When Antigravity (Gemini) delegates scoped coding, refactoring, scripting, or unit-test work to the local official **OpenAI Codex CLI (`codex exec`)**, this plugin pops open a live right-side monitor inside Antigravity streaming commands, syntax-colored diffs, model tiers, and token/cache telemetry every **0.8 seconds**—using pure local file polling with **zero extra LLM cost**, **zero CDP patching**, and full resilience across Antigravity updates.

> Agent deployment contract: **[`INSTALL_SKILL.md`](./INSTALL_SKILL.md)**

---

## Previews

![Antigravity Codex Monitor live demo](assets/demo.gif)

![Light theme preview](assets/preview-light.png)
![Dark theme preview](assets/preview-dark.png)

*(Open `plugin/ui/app.html?demo=1` or `?demo=1&theme=dark` locally in any browser for an interactive offline demo.)*

---

## Highlights

- **Live visibility for `codex exec`**: Streams active commands, exit codes, syntax-colored file diffs, and token/cache metrics every 0.8s at zero model cost.
- **Native MCP App (Zero Patch)**: Uses Antigravity's official plugin & MCP Apps (SEP-1865) API—no `.asar` edits, no CDP injection, no breakage on updates.
- **Cross-platform & self-contained**: Ships Windows (`run-codex.ps1`), macOS/Linux (`run-codex.sh`), a bundled `codex-worker` skill, and a prebuilt single-file HTML UI (**no Node.js required for end users**).
- **Dispatch from any chat**. Omit the target path to create a fresh task directory under `~/.gemini/antigravity/scratch/codex-tasks/`. Set `ANTIGRAVITY_SCRATCH_DIR` to override the scratch root.
- **Safe defaults**. Read-only and write tasks default to `read-only` and `workspace-write`, respectively. Trusted write tasks can opt into `-Sandbox full-access` / `--sandbox full-access`. Git write tasks default to `--worktree` isolation. Delegation has a two-round cap and requires user confirmation before merges or pushes.

---

## Quick Install

### Prerequisites
- **Google Antigravity 2.x**
- **OpenAI Codex CLI** (official CLI, signed in via `codex login`)
- **Python 3.10+** with `mcp` 1.x or 2.x (`pip install mcp`)

### Option 1: One-Prompt Agent Install (Recommended)

Paste this prompt into Antigravity:

> Clone `https://github.com/Lyl101010/antigravity-codex-monitor.git`, read `INSTALL_SKILL.md`, and run `python install.py doctor --json`, `python install.py install --json`, and `python install.py verify --json` to deploy and verify the plugin.

### Option 2: Manual CLI Install

```bash
git clone https://github.com/Lyl101010/antigravity-codex-monitor.git
cd antigravity-codex-monitor

python3 install.py doctor
python3 install.py install
python3 install.py verify
```

### Uninstall

```bash
python3 install.py uninstall
```

---

## Security & Compliance

- **Single-direction only**: `Antigravity -> official codex exec`. No reverse calls to personal Antigravity CLI (`agy`).
- **Zero credential access**: Never reads, exports, or proxies `~/.codex/auth.json`, OAuth tokens, or cookies.
- **Local monitoring**. The monitor reads local Codex dispatch and session logs and sends no telemetry.

## License

[MIT](./LICENSE)
