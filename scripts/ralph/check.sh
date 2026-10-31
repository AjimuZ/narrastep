#!/usr/bin/env bash
# Runs the project's checks for the named areas, the same commands for the agent and the runner.
#
# Usage: ./scripts/ralph/check.sh AREA [AREA...]
# Areas: docs backend web e2e desktop ralph compose stack fullstack
# Returns 0 when every command passed. Stops at the first failure with its exit status, and 2 for
# an unknown area. Prints a one-line result per command, so a log shows what really ran.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SANDBOX="${RALPH_SANDBOX:-$SCRIPT_DIR/sandbox.sh}"
STACK="${RALPH_STACK:-$SCRIPT_DIR/stack.sh}"

run_step() {
  local label="$1"
  shift
  local started=$SECONDS status=0
  echo "check: $label ..."
  "$@" || status=$?
  echo "check: $label -> $([ "$status" -eq 0 ] && echo passed || echo "FAILED ($status)") in $((SECONDS - started)) s"
  [ "$status" -eq 0 ] || exit "$status"
}

# What only an overloaded computer causes, never a wrong line of code: out of memory, a test worker
# that could not start, or a dev server that died. Other programs share Docker's memory here.
RESOURCE_FAILURE='ENOMEM|Cannot allocate memory|Timeout starting forks runner|ERR_CONNECTION_REFUSED|ERR_CONNECTION_RESET'
RESOURCE_RETRIES=2

# Runs a command in the sandbox and repeats it, after a pause, only when it failed for lack of
# resources, so that this does not cost the task an attempt. Any other failure counts at once.
in_sandbox() {
  local label="$1" command="$2" started=$SECONDS status tries=0 output
  output="$(mktemp)"
  echo "check: $label ..."
  while :; do
    "$SANDBOX" "$command" 2>&1 | tee "$output"
    status="${PIPESTATUS[0]}"
    [ "$status" -ne 0 ] && [ "$tries" -lt "$RESOURCE_RETRIES" ] && grep -qE "$RESOURCE_FAILURE" "$output" || break
    tries=$((tries + 1))
    echo "check: $label ran out of resources; trying again ($tries of $RESOURCE_RETRIES)"
    sleep "${RALPH_RESOURCE_WAIT_SECONDS:-60}"
  done
  rm -f "$output"
  echo "check: $label -> $([ "$status" -eq 0 ] && echo passed || echo "FAILED ($status)") in $((SECONDS - started)) s"
  [ "$status" -eq 0 ] || exit "$status"
}

check_stack() {
  # Builds and runs the real images under the isolated project of stack.sh, then always stops them.
  # The volumes of that project are removed too; the owner's project is a different one.
  # The image build itself runs the transcription and llama.cpp checks, so a build that passes has
  # passed them. The worker has no Compose health check, hence the wait for `status: ok`.
  local status=0
  echo "check: stack: build and start ..."
  "$STACK" up --build --detach --wait || status=$?
  if [ "$status" -eq 0 ]; then
    echo "check: stack: health endpoint ..."
    "$STACK" exec -T api python -c "$STACK_HEALTH_PROBE" || status=$?
  fi
  "$STACK" down --volumes --remove-orphans >/dev/null 2>&1 || true
  echo "check: stack -> $([ "$status" -eq 0 ] && echo passed || echo "FAILED ($status)")"
  [ "$status" -eq 0 ] || exit "$status"
}

# The opt-in browser journey of the `fullstack` CI job: the real stack with mock processing, which
# only this environment turns on. Always stops the stack and removes that project's volumes.
check_fullstack() {
  local status=0
  echo "check: fullstack: build and start ..."
  NARRASTEP_ENV=test NARRASTEP_ENABLE_MOCK_PROCESSING=true NARRASTEP_MIN_FREE_DISK_MB=64 \
    "$STACK" up --build --detach --wait || status=$?
  [ "$status" -eq 0 ] && { "$STACK" exec -T api python -c "$STACK_HEALTH_PROBE" || status=$?; }
  if [ "$status" -eq 0 ]; then
    echo "check: fullstack: browser journey ..."
    # The health answer asks the worker with a short timeout, so on a busy computer it can say the
    # worker is missing although it runs. Only that message repeats the journey; a real failure counts.
    local output tries=0
    output="$(mktemp)"
    while :; do
      status=0
      RALPH_SANDBOX_NETWORK=narrastep-ralph_local "$SANDBOX" \
        "cd apps/web && npx playwright install chromium && NARRASTEP_E2E_BASE_URL=http://web NARRASTEP_E2E_MOCK_CONFIRMED=true npm run --silent e2e:fullstack" 2>&1 | tee "$output"
      status="${PIPESTATUS[0]}"
      [ "$status" -ne 0 ] && [ "$tries" -lt 2 ] && grep -q 'The Celery worker must be available' "$output" || break
      tries=$((tries + 1))
      echo "check: fullstack: the worker's health answer was late; trying again ($tries of 2)"
      sleep "${RALPH_RESOURCE_WAIT_SECONDS:-30}"
    done
    rm -f "$output"
  fi
  "$STACK" down --volumes --remove-orphans >/dev/null 2>&1 || true
  echo "check: fullstack -> $([ "$status" -eq 0 ] && echo passed || echo "FAILED ($status)")"
  [ "$status" -eq 0 ] || exit "$status"
}

STACK_HEALTH_PROBE='
import json, time, urllib.request
body = {}
for _ in range(60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health", timeout=5) as response:
            body = json.load(response)
    except OSError as error:
        body = {"error": str(error)}
    if body.get("status") == "ok":
        break
    time.sleep(2)
print(body)
raise SystemExit(0 if body.get("status") == "ok" else 1)
'

cd "$ROOT" || exit 2

for area in "$@"; do
  case "$area" in
    docs)
      in_sandbox "docs: links" "python scripts/check_docs.py"
      ;;
    backend)
      in_sandbox "backend: format" "cd backend && python -m ruff format --check ."
      in_sandbox "backend: lint" "cd backend && python -m ruff check ."
      in_sandbox "backend: types" "cd backend && python -m mypy app"
      in_sandbox "backend: tests" "cd backend && python -B -m pytest -q -p no:cacheprovider"
      in_sandbox "backend: OpenAPI document" "python scripts/export_openapi.py --check"
      ;;
    web)
      in_sandbox "web: format" "cd apps/web && npm run --silent format:check"
      in_sandbox "web: lint" "cd apps/web && npm run --silent lint"
      in_sandbox "web: unit tests" "cd apps/web && npm run --silent test"
      in_sandbox "web: build and message check" "cd apps/web && npm run --silent build"
      in_sandbox "web: message extraction" "cd apps/web && npm run --silent check:i18n"
      in_sandbox "web: browser test types" "cd apps/web && npm run --silent typecheck:e2e"
      ;;
    e2e)
      in_sandbox "e2e: browser contract and accessibility" \
        "cd apps/web && npx playwright install chromium && npm run --silent e2e -- --reporter=dot --workers=4"
      ;;
    desktop)
      in_sandbox "desktop: format" \
        "apps/web/node_modules/.bin/prettier --check 'desktop/src/*.cjs' 'desktop/test/**/*.cjs' desktop/package.json"
      in_sandbox "desktop: tests" "cd desktop && npm test"
      ;;
    ralph)
      in_sandbox "ralph: runner tests" \
        "node --test 'scripts/ralph/test/*.test.cjs'"
      ;;
    compose)
      run_step "compose: configuration renders" "$STACK" config --quiet
      ;;
    stack)
      check_stack
      ;;
    fullstack)
      check_fullstack
      ;;
    *)
      echo "check: unknown area '$area'" >&2
      exit 2
      ;;
  esac
done
