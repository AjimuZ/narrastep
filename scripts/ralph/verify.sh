#!/usr/bin/env bash
# Verifies the current working tree for one task: the same steps for the agent and the runner.
#
# Usage: ./scripts/ralph/verify.sh TASK_ID
# Needs scripts/ralph/.state/result.json, which holds the commit message it checks.
# Returns 0 only when the static checks, the task's areas and its extra commands all passed; every
# other status is the failing step's. Commits nothing and changes no task state.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SANDBOX="${RALPH_SANDBOX:-$SCRIPT_DIR/sandbox.sh}"
PRD=(node "$SCRIPT_DIR/prd.cjs")

if [ "$#" -ne 1 ]; then
  echo "usage: verify.sh TASK_ID" >&2
  exit 2
fi
task="$1"
cd "$ROOT" || exit 2

"${PRD[@]}" preflight "$task" || exit 1

read -r -a areas <<< "$("${PRD[@]}" areas "$task" | tr -d '\r')"
"$SCRIPT_DIR/check.sh" "${areas[@]}" || exit $?

status=0
while IFS=$'\t' read -r where command; do
  [ -n "$where" ] || continue
  echo "check: task command ($where): $command"
  if [ "$where" = "sandbox" ]; then
    "$SANDBOX" "$command" </dev/null || status=$?
  else
    bash -c "$command" </dev/null || status=$?
  fi
  [ "$status" -eq 0 ] || { echo "check: task command FAILED ($status)"; exit "$status"; }
done < <("${PRD[@]}" verify-commands "$task" | tr -d '\r')

echo "verify: $task passed"
