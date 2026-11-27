---
name: install-antigravity-codex-monitor
description: Automated deployment, verification, and uninstallation contract for the Antigravity Codex Monitor native MCP App plugin (Windows, macOS, Linux).
---

# Antigravity Codex Monitor — AI Agent 部署契约

本文件面向执行自动安装的 AI Agent（Antigravity / Claude Code / Codex / Cursor 等）。仓库内已预编译单文件前端 `plugin/ui/app.html`，**最终用户安装全程无需 Node.js / npm**，只需 Python 3.10+（含 `mcp` 包）与本机已安装的官方 OpenAI Codex CLI。

## Step 1：前置环境诊断

在仓库根目录执行：

```bash
python3 install.py doctor --json
# Windows 若 python3 不可用，改用 python 或 py -3：
python install.py doctor --json
```

重点检查输出字段：
1. `python`：探测可用的 Python 解释器及 `mcp` 版本，支持 `mcp` 1.x（`FastMCP`）和 2.x（`MCPServer`）。
   - 若为 `null`：引导用户在目标 Python 环境中执行 `pip install mcp`（或指定已装好 `mcp` 的解释器路径 `--python "<path>"`），不要强行写入不可用的解释器。
2. `codex`：是否检测到本机官方 `codex` CLI。
   - 若为 `null`：提示用户先安装官方 OpenAI Codex CLI 并完成登录（`codex login`）。
3. `standalone_skill_conflict`：若为 `true`，说明用户本机已存在独立的 `~/.gemini/config/skills/codex-worker`。安装器不会覆盖它；告知用户如有需要可自行移除旧的独立目录以统一使用插件内嵌版本。

## Step 2：安装插件

```bash
python install.py install --json
# 如需显式指定 Python 解释器或目标目录：
python install.py install --python "<PYTHON_EXE_PATH>" --json
```

安装器会：
- 将自包含插件目录 `plugin/` 安装至 `~/.gemini/config/plugins/antigravity-codex-monitor/`；
- 生成指向已验证 Python 绝对路径的 `mcp_config.json`（不写死任何开发者个人路径）；
- 在 macOS / Linux 下自动为 `scripts/run-codex.sh` 赋予可执行权限。

## Step 3：真实 MCP 握手验收

```bash
python install.py verify --json
```

确认返回：
- `"verified": true`
- `"html_bytes"` > 0 且小于 10 MiB
- `"tools"` 包含 `"open_codex_monitor"` 与 `"get_codex_state"`

## Step 4：启用与交付指引

- Antigravity 会自动发现 `~/.gemini/config/plugins/antigravity-codex-monitor/`（如果当前运行会话尚未加载新 MCP 工具，引导用户刷新 MCP Servers 或重开一个新对话）。
- 在回复中可附上插件管理链接 `[antigravity-codex-monitor](customization://antigravity-codex-monitor)`，并提示用户若希望 Antigravity 默认自动将编码杂活派发给 Codex，可按需参考仓库内的 `examples/codex-delegation-rule.md`。

## 卸载

```bash
python install.py uninstall --json
```

仅移除 `~/.gemini/config/plugins/antigravity-codex-monitor/`，绝不触碰用户的 `~/.codex/` 会话日志或项目代码。
