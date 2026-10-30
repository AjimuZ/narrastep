#!/usr/bin/env bash
# Runs the project's checks for the named areas, the same commands for the agent and the runner.
#
# Usage: ./scripts/ralph/check.sh AREA [AREA...]
# Areas: docs backend web e2e desktop ralph compose stack
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

in_sandbox() {
  local label="$1"
  run_step "$label" "$SANDBOX" "$2"
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
        "cd apps/web && npx playwright install chromium && npm run --silent e2e -- --reporter=dot"
      ;;
    desktop)
      in_sandbox "desktop: format" \
        "apps/web/node_modules/.bin/prettier --check 'desktop/src/*.cjs' 'desktop/test/**/*.cjs' desktop/package.json"
      in_sandbox "desktop: tests" "cd desktop && npm test"
      ;;
    ralph)
      in_sandbox "ralph: runner tests and task list" \
        "node --test 'scripts/ralph/test/*.test.cjs' && node scripts/ralph/prd.cjs validate"
      ;;
    compose)
      run_step "compose: configuration renders" "$STACK" config --quiet
      ;;
    stack)
      check_stack
      ;;
    *)
      echo "check: unknown area '$area'" >&2
      exit 2
      ;;
  esac
done
