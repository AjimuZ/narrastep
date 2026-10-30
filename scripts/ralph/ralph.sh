#!/usr/bin/env bash
# Ralph - long-running development loop: one fresh Claude Code process per task.
# Adapted from https://github.com/snarktank/ralph (MIT, see LICENSE.upstream and README.md).
#
# Usage: ./scripts/ralph/ralph.sh [--tool claude] [--resume] [--dry-run] [max_iterations]
#
# The runner, not the agent, picks the task, verifies the result, commits and sets `passes`.
# Exit status: 0 every task an agent can do passed, 1 iteration limit reached, 2 not started,
# 3 stopped by repeated session failures, 4 repository changed behind the runner's back,
# 5 only blocked or owner tasks remain, 6 stopped on request.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="$SCRIPT_DIR/.state"
LOG_DIR="$STATE_DIR/logs"
RESULT_FILE="$STATE_DIR/result.json"
CURRENT_FILE="$STATE_DIR/current-task"
STOP_FILE="$STATE_DIR/stop"
PID_FILE="$STATE_DIR/run.pid"
RUN_LOG="$STATE_DIR/run.log"
PRD_FILE="$SCRIPT_DIR/prd.json"
PROGRESS_FILE="$SCRIPT_DIR/progress.txt"
PRD=(node "$SCRIPT_DIR/prd.cjs")

MAX_ITERATIONS=30
RESUME=0
DRY_RUN=0
TIMEOUT_SECONDS="${RALPH_ITERATION_TIMEOUT_SECONDS:-5400}"
LIMIT_WAIT_SECONDS="${RALPH_LIMIT_WAIT_SECONDS:-1800}"
LIMIT_MAX_WAITS="${RALPH_LIMIT_MAX_WAITS:-12}"
MAX_SESSION_FAILURES=2
VERIFY="${RALPH_VERIFY:-$SCRIPT_DIR/verify.sh}"
# What the agent must leave alone, apart from the state files that the runner itself changes.
RUNNER_OWN_FILES=(scripts/ralph ':(exclude)scripts/ralph/prd.json' ':(exclude)scripts/ralph/progress.txt' ':(exclude)scripts/ralph/archive' ':(exclude)scripts/ralph/.state')

log() {
  echo "ralph: $*"
  [ -d "$STATE_DIR" ] && echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$RUN_LOG"
  return 0
}

die() {
  echo "ralph: $*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tool) [ "${2:-}" = "claude" ] || die "only --tool claude is configured"; shift 2 ;;
    --tool=claude) shift ;;
    --resume) RESUME=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)
      [[ "$1" =~ ^[0-9]+$ ]] || die "unknown argument '$1'"
      MAX_ITERATIONS="$1"
      shift
      ;;
  esac
done

# Prints the Claude Code executable: RALPH_CLAUDE_BIN, `claude` on PATH, or the newest binary that
# the VS Code extension ships, so that nothing needs installing on this computer.
find_claude() {
  if [ -n "${RALPH_CLAUDE_BIN:-}" ]; then
    echo "$RALPH_CLAUDE_BIN"
    return 0
  fi
  if command -v claude >/dev/null 2>&1; then
    command -v claude
    return 0
  fi
  local home="${USERPROFILE:-$HOME}" found
  found="$(ls -d "$home"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude* 2>/dev/null | sort -V | tail -n 1)"
  [ -n "$found" ] || return 1
  echo "$found"
}

# Fails unless the repository, the tools and the task list allow a run.
preflight() {
  command -v node >/dev/null 2>&1 || die "node is required (the desktop window needs it too)"
  command -v git >/dev/null 2>&1 || die "git is required"
  [ -n "${CLAUDECODE:-}" ] && [ -z "${RALPH_ALLOW_NESTED:-}" ] && \
    die "this is a Claude Code session; start the runner from a separate terminal"
  CLAUDE_BIN="$(find_claude)" || die "Claude Code not found; set RALPH_CLAUDE_BIN to its executable"
  "${PRD[@]}" validate --committed || die "the task list is not valid"

  local wanted current
  wanted="$("${PRD[@]}" branch | tr -d '\r')"
  current="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  [ "$current" = "$wanted" ] || die "the runner works on '$wanted', but '$current' is checked out"
  # `git add --all` would otherwise commit the logs, and the reports would look like changes.
  git -C "$ROOT" check-ignore --quiet scripts/ralph/.state/probe || die "scripts/ralph/.state/ must be in .gitignore"

  # A roadmap behind the remote would make the agent redo delivered work. Offline is not an error.
  if git -C "$ROOT" remote get-url origin >/dev/null 2>&1 && git -C "$ROOT" fetch --quiet origin "$wanted" 2>/dev/null; then
    local behind
    behind="$(git -C "$ROOT" rev-list --count "HEAD..FETCH_HEAD" 2>/dev/null || echo 0)"
    [ "$behind" = "0" ] || die "'$wanted' is $behind commit(s) behind origin; integrate them first"
  fi

  if [ -n "$(git -C "$ROOT" status --porcelain)" ] && [ "$RESUME" -eq 0 ]; then
    die "the working tree has changes; commit them, or use --resume to continue an interrupted task"
  fi
  if [ "$RESUME" -eq 1 ] && [ -n "$(git -C "$ROOT" status --porcelain)" ] && [ ! -f "$CURRENT_FILE" ]; then
    die "the working tree has changes that no interrupted task owns; commit or move them first"
  fi

  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    die "a run is already active (process $(cat "$PID_FILE"))"
  fi
}

# Starts one agent process for the task, with a clean context and limited permissions.
run_agent() {
  local task="$1" prompt="$2" raw_log="$3" text_log="$4" event_file="$5"
  local arguments=(
    -p
    --output-format stream-json
    --verbose
    --permission-mode dontAsk
    --setting-sources project
    --settings scripts/ralph/settings.json
    --strict-mcp-config
    --no-session-persistence
    --tools "Bash,Read,Edit,Write,Glob,Grep,Skill"
  )
  [ -n "${RALPH_MODEL:-}" ] && arguments+=(--model "$RALPH_MODEL")
  [ -n "${RALPH_EFFORT:-}" ] && arguments+=(--effort "$RALPH_EFFORT")

  (cd "$ROOT" && timeout --kill-after=30 "$TIMEOUT_SECONDS" "$CLAUDE_BIN" "${arguments[@]}" < "$prompt" 2>&1) \
    | tee "$raw_log" \
    | "${PRD[@]}" stream "$event_file" \
    | tee "$text_log"
  return "${PIPESTATUS[0]}"
}

# Sets the working tree aside, keeping it recoverable, and commits the state change of a block.
record_block_in_git() {
  local task="$1"
  git -C "$ROOT" stash push --include-untracked --message "ralph blocked $task" -- . \
    ':(exclude)scripts/ralph/prd.json' ':(exclude)scripts/ralph/progress.txt' \
    ':(exclude)scripts/ralph/archive' >/dev/null
  git -C "$ROOT" add scripts/ralph/prd.json scripts/ralph/progress.txt
  # The history file exists only once old log entries have moved there.
  [ -d "$SCRIPT_DIR/archive" ] && git -C "$ROOT" add scripts/ralph/archive
  git -C "$ROOT" commit --quiet -m "Record task $task as blocked" \
    -m "Its work, if any, is in the Git stash; the reason is in scripts/ralph/prd.json." \
    -m "Task: $task" || log "could not commit the blocked state of $task"
  rm -f "$CURRENT_FILE"
}

# Counts a failed attempt. A second failure blocks the task, and independent tasks continue.
record_failure() {
  local task="$1" reason="$2" state
  state="$("${PRD[@]}" fail "$task" "$reason" | tr -d '\r')"
  if [ "$state" = "blocked" ]; then
    log "$task is blocked after repeated failures"
    record_block_in_git "$task"
  else
    log "$task failed; the next iteration retries it"
  fi
}

# Verifies the working tree and commits it. This is the only path that sets `passes`.
verify_and_commit() {
  local task="$1" verify_log="$2" message_file="$STATE_DIR/commit-message.txt" backup="$STATE_DIR/prd.backup.json"
  local progress_backup="$STATE_DIR/progress.backup.txt"
  log "$task: running verification, the output is in $verify_log"
  if ! "$VERIFY" "$task" > "$verify_log" 2>&1; then
    # The size keeps the reason within the Windows limit for one command line.
    record_failure "$task" "Verification failed:
$(tail -n 40 "$verify_log" | tail -c 3500)"
    return 1
  fi

  "${PRD[@]}" commit-message "$task" "$message_file" || return 1
  cp "$PRD_FILE" "$backup"
  cp "$PROGRESS_FILE" "$progress_backup"
  "${PRD[@]}" pass "$task"
  git -C "$ROOT" add --all
  if ! git -C "$ROOT" commit --quiet --file "$message_file"; then
    # A hook refused it: `passes` must not stay set on work that is not committed.
    git -C "$ROOT" reset --quiet
    cp "$backup" "$PRD_FILE"
    cp "$progress_backup" "$PROGRESS_FILE"
    record_failure "$task" "git commit was refused; see the hook's output in the run log"
    return 1
  fi
  rm -f "$CURRENT_FILE" "$backup" "$progress_backup"
  log "$task: passed and committed as $(git -C "$ROOT" rev-parse --short HEAD)"
}

cd "$ROOT" || exit 2
preflight
mkdir -p "$LOG_DIR"

if [ "$DRY_RUN" -eq 1 ]; then
  log "dry run: Claude Code at $CLAUDE_BIN, limit $MAX_ITERATIONS iterations"
  "${PRD[@]}" status
  exit 0
fi

[ -f "$PROGRESS_FILE" ] || { echo "# Ralph Progress Log" > "$PROGRESS_FILE"; printf '\n## Codebase Patterns\n---\n' >> "$PROGRESS_FILE"; }
rm -f "$STOP_FILE"
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
trap 'log "interrupted; continue with --resume"; exit 6' INT TERM

log "starting: Claude Code at $CLAUDE_BIN, limit $MAX_ITERATIONS iterations, resume=$RESUME"
session_failures=0
iteration=0

while [ "$iteration" -lt "$MAX_ITERATIONS" ]; do
  if [ -f "$STOP_FILE" ]; then
    log "stopped on request; continue with --resume"
    exit 6
  fi

  # An interrupted or failed task keeps its working tree, so it must continue before any other.
  task=""
  if [ -f "$CURRENT_FILE" ]; then
    task="$(cat "$CURRENT_FILE")"
    "${PRD[@]}" runnable "$task" 2>/dev/null || { rm -f "$CURRENT_FILE"; task=""; }
  fi
  if [ -z "$task" ]; then
    step="$("${PRD[@]}" next | tr -d '\r')"
    case "$step" in
      complete)
        log "every task an agent can do has passed its verification"
        "${PRD[@]}" status
        exit 0
        ;;
      stuck)
        log "only blocked tasks, owner tasks or tasks waiting for them remain"
        "${PRD[@]}" status
        exit 5
        ;;
      "run "*) task="${step#run }" ;;
      *) log "unexpected answer from the task list: $step"; exit 2 ;;
    esac
  fi

  iteration=$((iteration + 1))
  stamp="$(printf '%04d' "$iteration")-$(date +%Y%m%d-%H%M%S)-$task"
  log "iteration $iteration of $MAX_ITERATIONS: $task"
  echo "$task" > "$CURRENT_FILE"
  rm -f "$RESULT_FILE"

  prompt_file="$STATE_DIR/prompt.md"
  { cat "$SCRIPT_DIR/CLAUDE.md"; echo; "${PRD[@]}" brief "$task"; } > "$prompt_file"
  base_head="$(git rev-parse HEAD)"
  base_branch="$(git rev-parse --abbrev-ref HEAD)"
  prd_hash="$(sha256sum "$PRD_FILE" "$PROGRESS_FILE")"
  cp "$PRD_FILE" "$STATE_DIR/prd.before.json"
  cp "$PROGRESS_FILE" "$STATE_DIR/progress.before.txt"

  waits=0
  while :; do
    event_file="$STATE_DIR/last-event.json"
    rm -f "$event_file"
    run_agent "$task" "$prompt_file" "$LOG_DIR/$stamp.jsonl" "$LOG_DIR/$stamp.txt" "$event_file"
    agent_status=$?
    kind="$("${PRD[@]}" classify "$event_file" "$agent_status" "$(tail -c 2000 "$LOG_DIR/$stamp.jsonl" 2>/dev/null)" | tr -d '\r')"
    [ "$kind" = "limit" ] || break
    waits=$((waits + 1))
    if [ "$waits" -gt "$LIMIT_MAX_WAITS" ] || [ "$LIMIT_WAIT_SECONDS" -eq 0 ]; then
      log "the usage limit did not clear; continue later with --resume"
      exit 3
    fi
    log "usage limit reached; waiting $LIMIT_WAIT_SECONDS s (wait $waits of $LIMIT_MAX_WAITS), no attempt counted"
    sleep "$LIMIT_WAIT_SECONDS"
    [ -f "$STOP_FILE" ] && { log "stopped on request; continue with --resume"; exit 6; }
  done

  # The agent has no permission to change history, so a moved HEAD means someone else did.
  if [ "$(git rev-parse HEAD)" != "$base_head" ] || [ "$(git rev-parse --abbrev-ref HEAD)" != "$base_branch" ]; then
    log "HEAD or the branch changed during the iteration; stopping so nothing is mixed up"
    exit 4
  fi
  # Task state belongs to the runner: a changed copy is restored and costs the attempt. The copy
  # comes from before the iteration, not from HEAD, which lacks the uncommitted attempt counters.
  if [ "$prd_hash" != "$(sha256sum "$PRD_FILE" "$PROGRESS_FILE")" ]; then
    cp "$STATE_DIR/prd.before.json" "$PRD_FILE"
    cp "$STATE_DIR/progress.before.txt" "$PROGRESS_FILE"
    record_failure "$task" "The agent changed prd.json or progress.txt, which only the runner may change"
    continue
  fi
  tampered="$(git status --porcelain -- "${RUNNER_OWN_FILES[@]}" || true)"
  if [ -n "$tampered" ]; then
    git restore --source=HEAD --staged --worktree -- "${RUNNER_OWN_FILES[@]}" 2>/dev/null
    git clean -fdq -- "${RUNNER_OWN_FILES[@]}"
    record_failure "$task" "The agent changed the runner's own files: $tampered"
    continue
  fi

  if [ "$agent_status" -eq 124 ] || [ "$agent_status" -eq 137 ]; then
    record_failure "$task" "The session did not finish in $TIMEOUT_SECONDS s; the task may be too large"
    continue
  fi
  if [ "$kind" = "error" ] && [ ! -f "$RESULT_FILE" ]; then
    # Not the task's fault, such as a lost login: no attempt counted, and the run stops if it repeats.
    session_failures=$((session_failures + 1))
    log "the session failed (status $agent_status, failure $session_failures of $MAX_SESSION_FAILURES): see $LOG_DIR/$stamp.txt"
    [ "$session_failures" -lt "$MAX_SESSION_FAILURES" ] && continue
    log "stopping after repeated session failures; continue with --resume"
    exit 3
  fi
  session_failures=0

  if ! outcome="$("${PRD[@]}" result 2>&1)"; then
    record_failure "$task" "The session ended without a usable report: $outcome"
    continue
  fi
  outcome="$(echo "$outcome" | tr -d '\r')"
  case "$outcome" in
    done)
      verify_and_commit "$task" "$LOG_DIR/$stamp.verify.log"
      ;;
    failed)
      record_failure "$task" "$("${PRD[@]}" result-reason)"
      ;;
    blocked)
      "${PRD[@]}" block "$task" "$("${PRD[@]}" result-reason)"
      log "$task is blocked by the agent's report"
      record_block_in_git "$task"
      ;;
  esac
  sleep "${RALPH_PAUSE_SECONDS:-2}"
done

log "reached the limit of $MAX_ITERATIONS iterations; continue with --resume"
"${PRD[@]}" status
exit 1
