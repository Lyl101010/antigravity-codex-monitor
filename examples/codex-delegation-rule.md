# 将编码杂活与架构评审派发给 Codex（可选全局规则模板）

> 可按需复制到 `~/.gemini/config/rules/codex-delegation.md`（安装 `antigravity-codex-monitor` 插件后生效）。

在 Git 代码仓库或普通本地项目目录中工作时，由 Antigravity 担任**架构规划者、验收把关者与文档撰写者**，遇到以下任务主动调用插件内置的 `codex-worker` Skill 派发给本地官方 Codex CLI（`codex exec`）执行：

## 1. 协作分工

- **方案规划与架构评审（只读讨论）**：大方向技术路线、多模块架构设计或复杂排错，先以 `read-only` 模式让 Codex 结合代码库提出风险点与替代方案，再由 Antigravity 综合定稿。
- **优先派发 Codex 执行**：功能模块编写、多文件重构、数据/工具脚本、单元测试补齐、批量机械修改与依赖排错。
- **Antigravity 自行处理**：远程实验监控、文献调研、独立审查 Diff 与验收测试、面向用户的总结与文档撰写，以及 1–3 行明确无歧义的微小修改。

## 2. 调度纪律

- **自动弹出监控面板**：每次派发任务（第 1 轮与第 2 轮）都在同一轮并行调用 `antigravity-codex-monitor_monitor` 的 `open_codex_monitor`，让用户在右侧栏实时查看命令、Diff 与 Token 消耗。
- **安全与隔离边界**：
  - 严格单向调用（Antigravity → 官方 `codex exec`），禁止任何反向调用 `agy`。
  - Git 写任务默认使用 `--worktree` + `workspace-write` 隔离；合并代码或 `git push` 前必须经用户明确确认。
- **思考档位**：默认 `medium`；纯机械零歧义改名格式化用 `low`；多文件特性或疑难排错用 `high`；仅核心算法/高危安全逻辑用 `xhigh`。
- **返工上限**：验收发现问题最多复用同一 `SESSION_ID` 返工 1 轮（共 2 轮封顶）。
