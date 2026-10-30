#!/usr/bin/env bash
# Shows the run: whether it is active, its current task, and passed, open and blocked tasks.
#
# Usage: ./scripts/ralph/status.sh [--watch [seconds]]
# Reads state only. Returns 0, or 2 when the task list cannot be read.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$SCRIPT_DIR/.state"

show() {
  if [ -f "$STATE_DIR/run.pid" ] && kill -0 "$(cat "$STATE_DIR/run.pid")" 2>/dev/null; then
    echo "Run: active (process $(cat "$STATE_DIR/run.pid"))$([ -f "$STATE_DIR/stop" ] && echo ', stopping after this iteration')"
  else
    echo "Run: not active"
  fi
  [ -f "$STATE_DIR/current-task" ] && echo "Current task: $(cat "$STATE_DIR/current-task")"
  node "$SCRIPT_DIR/prd.cjs" status || return 2
  if git remote get-url origin >/dev/null 2>&1; then
    echo
    echo "Commits waiting to be pushed: $(git rev-list --count "origin/main..HEAD" 2>/dev/null || echo unknown)"
    node "$SCRIPT_DIR/prd.cjs" ci-status
  fi
  if [ -f "$STATE_DIR/run.log" ]; then
    echo
    echo "Last run events:"
    tail -n 5 "$STATE_DIR/run.log"
  fi
  local latest
  latest="$(ls -t "$STATE_DIR"/logs/*.txt 2>/dev/null | head -n 1)"
  [ -n "$latest" ] && echo "Agent log: $latest"
  return 0
}

if [ "${1:-}" = "--watch" ]; then
  while :; do
    clear
    show
    sleep "${2:-30}"
  done
fi
show
