#!/usr/bin/env bash
set -euo pipefail
target='' prompt='' prompt_file='' resume='' mode=write effort=medium model='' in_place=0 sandbox=''
dispatch_id='' dispatch_supplied=0
while (($#)); do
  case "$1" in
    --target-path) target=${2?}; shift 2;; --prompt) prompt=${2:?}; shift 2;;
    --prompt-file) prompt_file=${2:?}; shift 2;; --resume) resume=${2:?}; shift 2;;
    --mode) mode=${2:?}; shift 2;; --effort) effort=${2:?}; shift 2;;
    --model) model=${2?}; shift 2;; --in-place) in_place=1; shift;;
    --sandbox) sandbox=${2?}; shift 2;;
    --dispatch-id) dispatch_id=${2?}; dispatch_supplied=1; shift 2;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done
case "$mode" in read-only|write) ;; *) exit 2;; esac
case "$effort" in low|medium|high|xhigh) ;; *) exit 2;; esac
case "$sandbox" in
  '') if [[ "$mode" == write ]]; then sandbox=workspace-write; else sandbox=read-only; fi;;
  read-only|workspace-write|full-access) ;;
  *) printf 'Sandbox must be read-only, workspace-write, or full-access\n' >&2; exit 2;;
esac
if [[ "$mode" == read-only && "$sandbox" != read-only ]]; then
  printf 'Read-only mode requires the read-only sandbox\n' >&2
  exit 2
fi
if [[ -n "$prompt_file" ]]; then prompt=$(cat "$prompt_file"); fi
[[ -n "$prompt" ]] || { printf 'Prompt is required\n' >&2; exit 2; }
codex=$(command -v codex || true)
if [[ -z "$codex" ]]; then for p in "$HOME/.local/bin/codex" "$HOME/.npm-global/bin/codex" /usr/local/bin/codex /opt/homebrew/bin/codex; do if [[ -x "$p" ]]; then codex=$p; break; fi; done; fi
[[ -n "$codex" ]] || { printf 'codex not found\n' >&2; exit 2; }
python=$(command -v python3 || command -v python || true)
[[ -n "$python" ]] || { printf 'Python 3 is required to record dispatches\n' >&2; exit 2; }
if ((dispatch_supplied)); then
  printf '%s' "$dispatch_id" | "$python" -c 'import sys, unicodedata
value = sys.stdin.buffer.read().decode("utf-8")
if not value or len(value) > 128 or any(c.isspace() or unicodedata.category(c) == "Cc" for c in value):
    sys.exit("Invalid dispatch ID")'
else
  dispatch_id=$("$python" -c 'import uuid; print(uuid.uuid4())')
fi
scratch_task=0
if [[ -z "$target" ]]; then scratch_task=1; fi
if [[ -d "$target" ]]; then
  target=$(CDPATH= cd -- "$target" && pwd -P)
else
  scratch_root=${ANTIGRAVITY_SCRATCH_DIR:-$HOME/.gemini/antigravity/scratch}
  target=$("$python" -c '
from datetime import datetime
from pathlib import Path
import sys, uuid

scratch = Path(sys.argv[1]).resolve()
requested = sys.argv[2]
target = (Path(requested) if requested else scratch / "codex-tasks").resolve()
if not target.is_relative_to(scratch):
    sys.exit("Target directory is required")
if not requested:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target /= f"task-{stamp}-{uuid.uuid4().hex[:8]}"
try:
    target.mkdir(parents=True, exist_ok=bool(requested))
except (FileExistsError, NotADirectoryError):
    sys.exit("Target directory is required")
print(target.as_posix())
' "$scratch_root" "$target")
fi
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_HOME"
started=$("$python" -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())')
is_git=0
if (( ! scratch_task )); then
  root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null || true)
  if [[ -n "$root" ]]; then is_git=1; target=$root; fi
fi
args=(exec)
if [[ -z "$resume" ]]; then args+=(-C "$target"); fi
if [[ "$sandbox" == full-access ]]; then args+=(--dangerously-bypass-approvals-and-sandbox); else args+=(-s "$sandbox"); fi
args+=(-c "model_reasoning_effort=\"$effort\"")
if (( ! is_git )); then args+=(--skip-git-repo-check); fi
if (( is_git && ! in_place )) && [[ "$mode" == write && -z "$resume" ]]; then args+=(--worktree); fi
if [[ -n "$model" ]]; then args+=(-m "$model"); fi
if [[ -n "$resume" ]]; then args+=(resume "$resume"); fi
args+=(--json -)
session=$resume
update() {
  printf '%s\n%s' "$dispatch_id" "$prompt" | MONITOR_TARGET="$target" MONITOR_MODE="$mode" MONITOR_SANDBOX="$sandbox" \
    MONITOR_MODEL="$model" MONITOR_EFFORT="$effort" MONITOR_STARTED="$started" MONITOR_SESSION="$session" \
    MONITOR_STATUS="$1" MONITOR_EXIT="${2:-}" "$python" -c '
import json, os, pathlib, sys, tempfile, time
e = os.environ
p = pathlib.Path(e["CODEX_HOME"]) / "codex-worker-dispatches.json"
dispatch_id = sys.stdin.buffer.readline().decode("utf-8").rstrip("\n")
preview = sys.stdin.buffer.read().decode("utf-8", errors="replace")[:4000]
entry = dict(dispatch_id=dispatch_id, direction="ag_to_codex", session_id=e["MONITOR_SESSION"],
             target_root=e["MONITOR_TARGET"], mode=e["MONITOR_MODE"], model=e["MONITOR_MODEL"],
             sandbox=e["MONITOR_SANDBOX"],
             effort=e["MONITOR_EFFORT"], started_at=e["MONITOR_STARTED"], prompt_preview=preview,
             status=e["MONITOR_STATUS"], exit_code=e["MONITOR_EXIT"])
lock = p.with_suffix(".json.lock")
deadline = time.monotonic() + 10
while True:
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        break
    except FileExistsError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.025)
try:
    try:
        entries = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        entries = []
    if not isinstance(entries, list):
        raise ValueError("invalid dispatch log")
    entries = [x for x in entries if x.get("dispatch_id") != entry["dispatch_id"]] + [entry]
    tmp_fd, tmp = tempfile.mkstemp(prefix=".dispatch-", dir=p.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(entries[-200:], f, ensure_ascii=False)
        for attempt in range(20):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
finally:
    os.close(fd)
    lock.unlink()
'
}
update running
printf 'DISPATCH_ID: %s\nSESSION_ID: %s\nWORKDIR: %s\nMODEL: %s\nEFFORT: %s\n' "$dispatch_id" "$resume" "$target" "${model:-config default}" "$effort"
session_file=$(mktemp "${TMPDIR:-/tmp}/codex-monitor.XXXXXXXX")
trap 'rm -f "$session_file"' EXIT
stream_output() {
  local line thread_id
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '%s\n' "$line"
    if [[ "$line" == *'"thread.started"'* ]]; then
      thread_id=$(printf '%s' "$line" | "$python" -c 'import json, sys
try:
    event = json.load(sys.stdin)
    if isinstance(event, dict) and event.get("type") == "thread.started":
        print(event.get("thread_id") or "")
except ValueError:
    pass')
      if [[ -n "$thread_id" && "$thread_id" != "$session" ]]; then
        session=$thread_id
        printf '%s' "$session" > "$session_file"
        update running
      fi
    fi
  done
}
set +e
printf '%s' "$prompt" | "$codex" "${args[@]}" | stream_output
status=${PIPESTATUS[1]}
set -e
session=$(cat "$session_file")
session=${session:-$resume}
if ((status == 0)); then update completed "$status"; else update failed "$status"; fi
printf 'DISPATCH_ID: %s\nSESSION_ID: %s\nWORKDIR: %s\nEXIT_CODE: %s\nMODEL: %s\nEFFORT: %s\n' "$dispatch_id" "$session" "$target" "$status" "${model:-config default}" "$effort"
exit "$status"
