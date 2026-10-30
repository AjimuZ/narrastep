#!/usr/bin/env bash
# Stops the run. By default it ends after the current iteration; --now ends it at once.
#
# Usage: ./scripts/ralph/stop.sh [--now]
# Either way task state and the working tree stay as they are, and `ralph.sh --resume` continues.
# Returns 0, also when no run is active.

set -uo pipefail

STATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.state"
mkdir -p "$STATE_DIR"
touch "$STATE_DIR/stop"

pid="$(cat "$STATE_DIR/run.pid" 2>/dev/null || true)"
if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$STATE_DIR/stop"
  echo "No active run."
  exit 0
fi

if [ "${1:-}" != "--now" ]; then
  echo "The run stops after the current iteration."
  exit 0
fi

# On Windows the agent is a native process tree, which only taskkill ends as a whole.
windows_pid="$(cat "/proc/$pid/winpid" 2>/dev/null || true)"
if [ -n "$windows_pid" ] && command -v taskkill >/dev/null 2>&1; then
  MSYS_NO_PATHCONV=1 taskkill /PID "$windows_pid" /T /F >/dev/null 2>&1 || true
else
  kill "$pid" 2>/dev/null || true
fi
rm -f "$STATE_DIR/run.pid"
echo "Stopped. Continue with ./scripts/ralph/ralph.sh --resume"
