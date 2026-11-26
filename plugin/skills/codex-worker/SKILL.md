---
name: codex-worker
description: >-
  Delegate scoped coding, refactoring, helper script, or unit-test tasks from
  Antigravity to the local official OpenAI Codex CLI (`codex exec`) while
  streaming live commands, syntax-colored diffs, and token telemetry inside the
  Antigravity Codex Monitor side pane.
---

# Codex Worker (Antigravity Plans & Reviews, Codex Executes)

Delegate well-scoped implementation, refactoring, testing, or read-only architecture review tasks to the local official `codex` CLI via the bundled cross-platform wrapper scripts, while Antigravity stays responsible for planning, diff inspection, independent verification, and user-facing communication.

## Mandatory Visibility

Whenever you dispatch a task to Codex (Round 1 or Round 2):

1. In the **same turn**, call the MCP App tool `antigravity-codex-monitor_monitor` / `open_codex_monitor` so Antigravity automatically opens or focuses the live **Codex Monitor** side pane.
2. Briefly inform the user in chat about the task goal, target directory, and allowed file scope.
3. After Codex finishes, inspect the diff/files and run key verification checks independently before reporting back.

## Guardrails

1. **Single-direction & sandbox-locked**: Strictly `Antigravity -> official codex exec` only. Sandbox is locked to `read-only` or `workspace-write` (never `danger-full-access`).
2. **Git worktree isolation**: For Git repositories, `write` tasks run inside an isolated `--worktree` by default so the main working tree stays untouched (pass `-InPlace` / `--in-place` only when explicitly requested). Non-Git folders automatically use `--skip-git-repo-check`.
3. **Effort tiers**:
   - `low`: strictly mechanical, zero-ambiguity edits.
   - `medium` (default): daily helper scripts, unit tests, single-module bug fixes, small features, read-only architecture reviews.
   - `high`: multi-file features, refactors, tricky debugging.
   - `xhigh`: high-stakes core algorithms, security, or concurrency logic only.
4. **Silent executor prompt**: Instruct Codex to edit ONLY the specified scope, run the exact verification command(s), avoid unsolicited prose/docs, and return a terse summary (changed files + commands + pass/fail).
5. **2-round cap & human confirmation gate**: At most 1 follow-up retry using the same `SESSION_ID`. Never merge/apply worktree changes or run `git push` without explicit user approval.

## Dispatch Commands

Write your scoped task prompt to a temporary file first to avoid shell quoting issues, then invoke the platform script:

### Windows (PowerShell)

```powershell
# Write task (Git worktree automatically)
powershell -ExecutionPolicy Bypass -File "$HOME\.gemini\config\plugins\antigravity-codex-monitor\scripts\run-codex.ps1" `
  -TargetPath "<PROJECT_OR_REPO_ROOT>" `
  -Mode write `
  -Effort medium `
  -PromptFile "<TEMP_PROMPT_FILE>"

# Read-only review
powershell -ExecutionPolicy Bypass -File "$HOME\.gemini\config\plugins\antigravity-codex-monitor\scripts\run-codex.ps1" `
  -TargetPath "<PROJECT_OR_REPO_ROOT>" `
  -Mode read-only `
  -Effort medium `
  -PromptFile "<TEMP_PROMPT_FILE>"

# Round 2 follow-up (same session)
powershell -ExecutionPolicy Bypass -File "$HOME\.gemini\config\plugins\antigravity-codex-monitor\scripts\run-codex.ps1" `
  -TargetPath "<PROJECT_OR_REPO_ROOT>" `
  -Mode write `
  -Effort medium `
  -ResumeSessionId "<SESSION_ID>" `
  -PromptFile "<ROUND_2_PROMPT_FILE>"
```

### macOS / Linux (Bash)

```bash
# Write task (Git worktree automatically)
~/.gemini/config/plugins/antigravity-codex-monitor/scripts/run-codex.sh \
  --target-path "<PROJECT_OR_REPO_ROOT>" \
  --mode write \
  --effort medium \
  --prompt-file "<TEMP_PROMPT_FILE>"

# Read-only review
~/.gemini/config/plugins/antigravity-codex-monitor/scripts/run-codex.sh \
  --target-path "<PROJECT_OR_REPO_ROOT>" \
  --mode read-only \
  --effort medium \
  --prompt-file "<TEMP_PROMPT_FILE>"

# Round 2 follow-up (same session)
~/.gemini/config/plugins/antigravity-codex-monitor/scripts/run-codex.sh \
  --target-path "<PROJECT_OR_REPO_ROOT>" \
  --mode write \
  --effort medium \
  --resume "<SESSION_ID>" \
  --prompt-file "<ROUND_2_PROMPT_FILE>"
```
