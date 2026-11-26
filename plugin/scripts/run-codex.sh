#!/usr/bin/env bash
set -euo pipefail
target='' prompt='' prompt_file='' resume='' mode=write effort=medium model='' in_place=0
while (($#)); do
  case "$1" in
    --target-path) target=${2:?}; shift 2;; --prompt) prompt=${2:?}; shift 2;;
    --prompt-file) prompt_file=${2:?}; shift 2;; --resume) resume=${2:?}; shift 2;;
    --mode) mode=${2:?}; shift 2;; --effort) effort=${2:?}; shift 2;;
    --model) model=${2:?}; shift 2;; --in-place) in_place=1; shift;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done
case "$mode" in read-only|write) ;; *) exit 2;; esac
case "$effort" in low|medium|high|xhigh) ;; *) exit 2;; esac
[[ -d "$target" ]] || { printf 'Target directory is required\n' >&2; exit 2; }
target=$(cd "$target" && pwd -P)
if [[ -n "$prompt_file" ]]; then prompt=$(cat "$prompt_file"); fi
[[ -n "$prompt" ]] || { printf 'Prompt is required\n' >&2; exit 2; }
codex=$(command -v codex || true)
if [[ -z "$codex" ]]; then for p in "$HOME/.local/bin/codex" "$HOME/.npm-global/bin/codex" /usr/local/bin/codex /opt/homebrew/bin/codex; do if [[ -x "$p" ]]; then codex=$p; break; fi; done; fi
[[ -n "$codex" ]] || { printf 'codex not found\n' >&2; exit 2; }
python=$(command -v python3 || command -v python || true)
[[ -n "$python" ]] || { printf 'Python 3 is required to record dispatches\n' >&2; exit 2; }
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_HOME"
dispatch_id=$($python -c 'import uuid; print(uuid.uuid4())')
started=$($python -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())')
root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null || true)
is_git=0
if [[ -n "$root" ]]; then is_git=1; target=$root; fi
sandbox=read-only
if [[ "$mode" == write ]]; then sandbox=workspace-write; fi
args=(exec -C "$target" -s "$sandbox" -c "model_reasoning_effort=\"$effort\"")
if (( ! is_git )); then args+=(--skip-git-repo-check); fi
if (( is_git && ! in_place )) && [[ "$mode" == write ]]; then args+=(--worktree); fi
if [[ -n "$model" ]]; then args+=(-m "$model"); fi
if [[ -n "$resume" ]]; then args+=(resume "$resume"); fi
args+=(--json -)
export MONITOR_ID="$dispatch_id" MONITOR_TARGET="$target" MONITOR_MODE="$mode" MONITOR_MODEL="$model" MONITOR_EFFORT="$effort" MONITOR_STARTED="$started" MONITOR_PROMPT="$prompt" MONITOR_SESSION="$resume"
update() {
  MONITOR_STATUS="$1" MONITOR_EXIT="${2:-}" "$python" -c '
import json, os, pathlib, tempfile
p=pathlib.Path(os.environ["CODEX_HOME"])/"codex-worker-dispatches.json"
try: entries=json.loads(p.read_text(encoding="utf-8"))
except FileNotFoundError: entries=[]
if not isinstance(entries,list): raise ValueError("invalid dispatch log")
e=os.environ
entry=dict(dispatch_id=e["MONITOR_ID"],direction="ag_to_codex",session_id=e["MONITOR_SESSION"],target_root=e["MONITOR_TARGET"],mode=e["MONITOR_MODE"],model=e["MONITOR_MODEL"],effort=e["MONITOR_EFFORT"],started_at=e["MONITOR_STARTED"],prompt_preview=e["MONITOR_PROMPT"][:4000],status=e["MONITOR_STATUS"],exit_code=e["MONITOR_EXIT"])
entries=[x for x in entries if x.get("dispatch_id") != entry["dispatch_id"]]+[entry]
fd,tmp=tempfile.mkstemp(prefix=".dispatch-",dir=p.parent)
try:
 with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(entries[-200:],f,ensure_ascii=False)
 os.replace(tmp,p)
finally:
 if os.path.exists(tmp): os.unlink(tmp)
'
}
update running
printf 'SESSION_ID: %s\nWORKDIR: %s\nMODEL: %s\nEFFORT: %s\n' "$resume" "$target" "${model:-config default}" "$effort"
log=$(mktemp "${TMPDIR:-/tmp}/codex-monitor.XXXXXXXX")
trap 'rm -f "$log"' EXIT
set +e
printf '%s' "$prompt" | "$codex" "${args[@]}" | tee "$log"
status=${PIPESTATUS[1]}
set -e
session=$($python -c 'import json,sys
for line in open(sys.argv[1],encoding="utf-8"):
 try:
  x=json.loads(line)
  if x.get("type")=="thread.started": print(x.get("thread_id","")); break
 except ValueError: pass' "$log")
export MONITOR_SESSION="${session:-$resume}"
if ((status == 0)); then update completed "$status"; else update failed "$status"; fi
printf 'SESSION_ID: %s\nWORKDIR: %s\nEXIT_CODE: %s\nMODEL: %s\nEFFORT: %s\n' "$MONITOR_SESSION" "$target" "$status" "${model:-config default}" "$effort"
exit "$status"
