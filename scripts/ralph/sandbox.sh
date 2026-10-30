#!/usr/bin/env bash
# Runs one command in the isolated check container, with the repository mounted at /work.
#
# Usage: ./scripts/ralph/sandbox.sh 'cd backend && python -m pytest tests/test_x.py'
#        ./scripts/ralph/sandbox.sh --build-only
# Returns the command's exit status, or 2 when Docker or the arguments are unusable.
# Guarantees: .git is read-only, and .env, data/ and .claude/ (apart from its skills) are hidden, so
# a command can neither change history nor read local settings or the owner's recordings.
# The container has network access for package registries, and no access outside the repository.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "sandbox: docker is not available" >&2
  exit 2
fi
if [ "$#" -ne 1 ]; then
  echo "usage: sandbox.sh 'command' | --build-only" >&2
  exit 2
fi

# Git Bash would rewrite the container paths below into Windows paths without this.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

if command -v cygpath >/dev/null 2>&1; then
  HOST_ROOT="$(cygpath -m "$ROOT")"
else
  HOST_ROOT="$ROOT"
fi

python_version="$(sed -n 's/^FROM .*python:\([0-9][0-9.]*\)-.*/\1/p' "$ROOT/backend/Dockerfile" | head -n 1)"
node_version="$(tr -d '[:space:]v' < "$ROOT/.nvmrc")"
playwright_version="$(sed -n 's/.*"@playwright\/test": "[^0-9]*\([0-9][0-9.]*\)".*/\1/p' "$ROOT/apps/web/package.json" | head -n 1)"
fingerprint="$(cat "$SCRIPT_DIR/checks.Dockerfile" | { cat; printf '%s %s %s' "$python_version" "$node_version" "$playwright_version"; } | sha256sum | cut -c1-12)"
IMAGE="narrastep-ralph-checks:$fingerprint"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "sandbox: building $IMAGE (Python $python_version, Node.js $node_version, Playwright $playwright_version)" >&2
  docker build --quiet \
    --build-arg "PYTHON_VERSION=$python_version" \
    --build-arg "NODE_VERSION=$node_version" \
    --build-arg "PLAYWRIGHT_VERSION=$playwright_version" \
    --tag "$IMAGE" \
    --file "$HOST_ROOT/scripts/ralph/checks.Dockerfile" \
    "$HOST_ROOT/scripts/ralph" >&2
fi

if [ "$1" = "--build-only" ]; then
  exit 0
fi

mounts=(
  --volume "$HOST_ROOT:/work"
  --volume "$HOST_ROOT/.git:/work/.git:ro"
  # Dependencies live in named volumes: the Linux packages must not mix with the host's.
  --volume "narrastep-ralph-venv:/opt/venv"
  --volume "narrastep-ralph-web-modules:/work/apps/web/node_modules"
  --volume "narrastep-ralph-desktop-modules:/work/desktop/node_modules"
  --volume "narrastep-ralph-cache:/cache"
)
# Each hidden path is mounted only when it exists, so the mount never creates it on the host.
[ -f "$ROOT/.env" ] && mounts+=(--volume "/dev/null:/work/.env:ro")
[ -d "$ROOT/data" ] && mounts+=(--tmpfs "/work/data")
[ -d "$ROOT/backend/.venv" ] && mounts+=(--tmpfs "/work/backend/.venv")
if [ -d "$ROOT/.claude" ]; then
  mounts+=(--tmpfs "/work/.claude")
  [ -d "$ROOT/.claude/skills" ] && mounts+=(--volume "$HOST_ROOT/.claude/skills:/work/.claude/skills:ro")
fi

# --init reaps orphaned processes: tests that wait for a stopped process would otherwise see a zombie.
exec docker run --rm --interactive --init \
  "${mounts[@]}" \
  --workdir /work \
  --env "CI=${RALPH_SANDBOX_CI:-}" \
  --env PIP_CACHE_DIR=/cache/pip \
  --env npm_config_cache=/cache/npm \
  --env PLAYWRIGHT_BROWSERS_PATH=/cache/ms-playwright \
  --env PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  bash /work/scripts/ralph/sandbox-entry.sh "$1"
