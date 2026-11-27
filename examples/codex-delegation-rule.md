# 将编码杂活与架构评审派发给 Codex（可选全局规则模板）

> 可按需复制到 `~/.gemini/config/rules/codex-delegation.md`（安装 `antigravity-codex-monitor` 插件后生效）。

本规则适用于所有对话，包括未打开工作区的聊天。Antigravity 负责规划、验收和文档撰写，通过 `codex-worker` Skill 将以下任务派发给本地官方 Codex CLI（`codex exec`）。未打开工作区时，省略 `-TargetPath` / `--target-path`，启动器会在 scratch 下自动创建任务目录。

## 1. 协作分工

- **方案与架构评审**。Codex 以 `read-only` 模式评审技术路线、架构或排错方案，由 Antigravity 综合定稿。
- **本地实现与绘图**。Codex 负责功能开发、重构、测试和工具脚本，以及图表绘制和使用可用工具生成 AI 图像。
- **远程实验**。Codex 编写、修改和测试配套脚本及日志解析工具。Antigravity 通过 SSH 执行服务器任务，监控 GPU 和运行进度。
- **Antigravity 自行处理**。文献调研、独立审查 Diff 与验收测试、面向用户的总结与文档撰写，以及 1–3 行明确无歧义的微小修改。

## 2. 调度纪律

- **自动弹出/锁定监控面板**：每次派发任务（第 1 轮与第 2 轮）先预生成唯一 `dispatch_id` 传给脚本（`-DispatchId` / `--dispatch-id`），并在同一轮并行调用 `antigravity-codex-monitor_monitor` 的 `open_codex_monitor`（传入 `{"dispatch_id": "..."}`，第 2 轮续跑同时传 `"session_id"`），让右侧栏精准锁定并实时刷新当前任务。
- **安全与隔离边界**：
  - 严格单向调用（Antigravity → 官方 `codex exec`），禁止任何反向调用 `agy`。
  - Git 写任务默认使用 `--worktree` + `workspace-write` 隔离。用户明确要求不受沙箱限制的 CLI 访问时，可为写任务指定 `-Sandbox full-access` / `--sandbox full-access`。合并代码或 `git push` 前必须经用户明确确认。
- **思考档位**：默认 `medium`；纯机械零歧义改名格式化用 `low`；多文件特性或疑难排错用 `high`；仅核心算法/高危安全逻辑用 `xhigh`。
- **返工上限**：验收发现问题最多复用同一 `SESSION_ID` 返工 1 轮（共 2 轮封顶）。
