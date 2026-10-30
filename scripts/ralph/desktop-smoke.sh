#!/usr/bin/env bash
# Smoke test of the desktop window: the real start.sh, launcher and Electron on the real images, as
# docs/DEVELOPMENT.md describes for P07.2, under the isolated project of stack.sh.
#
# Usage: ./scripts/ralph/desktop-smoke.sh
# Prints one PASS or FAIL line per step and returns the number of failed steps. Windows open and
# close on the screen while it runs. It reads only window titles, never the screen, so nothing else
# on the desktop reaches the logs. It deletes only desktop/node_modules, to repeat a first launch.
# Windows only, because it reads the titles through PowerShell.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOGS="$SCRIPT_DIR/.state/desktop-smoke"
WEB_PORT=14200
EXPECTED_TITLE="Narrastep"
failures=0

cd "$ROOT" || exit 2
mkdir -p "$LOGS"

# The same isolation as stack.sh, through Compose's own variables, because start.sh takes no
# Compose options. The owner's .env is not read when an env file is named.
export COMPOSE_PROJECT_NAME=narrastep-ralph
export COMPOSE_PATH_SEPARATOR=';'
export COMPOSE_FILE='docker-compose.yml;scripts/ralph/stack.override.yml'
export COMPOSE_ENV_FILES=scripts/ralph/stack.env
export NARRASTEP_WEB_PORT="$WEB_PORT"

step() {
  local name="$1" result="$2" detail="${3:-}"
  echo "$result $name${detail:+ ($detail)}"
  [ "$result" = "PASS" ] || failures=$((failures + 1))
}

# Prints the titles of the visible Electron windows, one per line.
window_titles() {
  powershell.exe -NoProfile -NonInteractive -Command \
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-Process electron -ErrorAction SilentlyContinue | Where-Object { \$_.MainWindowTitle } | ForEach-Object { \$_.MainWindowTitle }" \
    2>/dev/null | tr -d '\r'
}

# Waits up to $1 seconds for a window whose title names the app; prints the title.
wait_for_window() {
  local waited=0 titles
  while [ "$waited" -lt "$1" ]; do
    titles="$(window_titles)"
    if echo "$titles" | grep -aq "$EXPECTED_TITLE"; then
      echo "$titles" | grep -a "$EXPECTED_TITLE" | head -n 1
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# Waits up to $1 seconds until no app window is left.
wait_for_no_window() {
  local waited=0
  while [ "$waited" -lt "$1" ]; do
    window_titles | grep -aq "$EXPECTED_TITLE" || return 0
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

close_window() {
  powershell.exe -NoProfile -NonInteractive -Command \
    "Get-Process electron -ErrorAction SilentlyContinue | Where-Object { \$_.MainWindowTitle } | ForEach-Object { [void]\$_.CloseMainWindow() }" \
    >/dev/null 2>&1
}

api_health() {
  curl --silent --max-time 5 "http://127.0.0.1:$WEB_PORT/api/v1/health" | grep -q '"status":"ok"'
}

wait_for_health() {
  local waited=0
  while [ "$waited" -lt "$1" ]; do
    api_health && return 0
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# Starts start.sh with the given options and log name, in the background; sets START_PID.
start_stack() {
  local log="$LOGS/$1.log"
  shift
  ./start.sh "$@" > "$log" 2>&1 &
  START_PID=$!
}

# Stops the stack the way another terminal would, and waits for start.sh to end.
stop_stack() {
  docker compose down --remove-orphans >/dev/null 2>&1
  wait "$START_PID" 2>/dev/null
}

# Always leaves nothing behind, also after a failure.
clean_up() {
  docker compose down --volumes --remove-orphans >/dev/null 2>&1
  close_window
}
trap clean_up EXIT

clean_up
rm -rf desktop/node_modules

echo "--- step 1: first launch installs Electron and opens one window after health"
start_stack first
if title="$(wait_for_window 900)"; then
  step "the window opened" PASS "title: $title"
else
  step "the window opened" FAIL "no window in 15 minutes; see $LOGS/first.log"
fi
if api_health; then step "the stack was healthy with the window open" PASS; else step "the stack was healthy with the window open" FAIL; fi
if grep -q "First launch: installing Electron" "$LOGS/first.log"; then step "the first launch announced the install" PASS; else step "the first launch announced the install" FAIL; fi

echo "--- step 2: closing the window leaves the stack running"
close_window
if wait_for_no_window 30; then step "the window closed" PASS; else step "the window closed" FAIL; fi
sleep 5
if api_health; then step "the stack kept running after the window closed" PASS; else step "the stack kept running after the window closed" FAIL; fi
stop_stack

echo "--- step 3: stopping the stack closes the window, and no install is repeated"
start_stack second -r
if title="$(wait_for_window 300)"; then step "the second launch opened the window" PASS "title: $title"; else step "the second launch opened the window" FAIL; fi
if grep -q "First launch: installing Electron" "$LOGS/second.log"; then step "the second launch installed nothing" FAIL; else step "the second launch installed nothing" PASS; fi
stop_stack
if wait_for_no_window 60; then step "the window closed with the stack" PASS; else step "the window closed with the stack" FAIL "still open 60 s after the stack stopped"; close_window; fi

echo "--- step 4: --headless opens no window"
start_stack headless -r --headless
if wait_for_health 300; then step "the headless stack became healthy" PASS; else step "the headless stack became healthy" FAIL; fi
sleep 10
if window_titles | grep -aq "$EXPECTED_TITLE"; then step "headless opened no window" FAIL; else step "headless opened no window" PASS; fi
if grep -q "Waiting for Narrastep" "$LOGS/headless.log"; then step "headless started no launcher" FAIL; else step "headless started no launcher" PASS; fi
stop_stack

echo "--- versions"
docker version --format 'docker {{.Server.Version}}' 2>/dev/null
docker compose version 2>/dev/null
echo "node $(node --version)"

echo "--- result: $failures failed step(s)"
exit "$failures"
