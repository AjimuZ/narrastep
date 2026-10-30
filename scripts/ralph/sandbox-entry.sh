#!/usr/bin/env bash
# Inside the check container: installs the locked dependencies when a lock changed, then runs the
# command given as the first argument. Fails with the install's status when a lock cannot install.

set -euo pipefail

lock_hash() {
  sha256sum "$@" | sha256sum | cut -c1-32
}

ensure_backend() {
  local wanted current
  wanted="$(python --version)-$(lock_hash backend/requirements-dev.lock)"
  current="$(cat /opt/venv/.ralph-lock 2>/dev/null || true)"
  [ "$wanted" = "$current" ] && return 0
  echo "sandbox: installing backend/requirements-dev.lock" >&2
  find /opt/venv -mindepth 1 -delete
  python -m venv /opt/venv
  /opt/venv/bin/python -m pip install --quiet --require-hashes --no-deps --only-binary=:all: \
    -r backend/requirements-dev.lock
  /opt/venv/bin/python -m pip install --quiet --no-deps --no-build-isolation --no-index -e ./backend
  printf '%s' "$wanted" > /opt/venv/.ralph-lock
}

ensure_node_folder() {
  local folder="$1" install="$2" wanted current
  wanted="$(node --version)-$(lock_hash "$folder/package-lock.json")"
  current="$(cat "$folder/node_modules/.ralph-lock" 2>/dev/null || true)"
  [ "$wanted" = "$current" ] && return 0
  echo "sandbox: installing $folder/package-lock.json" >&2
  # `npm ci` would try to remove the mounted folder itself, so only its contents go.
  find "$folder/node_modules" -mindepth 1 -delete
  (cd "$folder" && $install)
  printf '%s' "$wanted" > "$folder/node_modules/.ralph-lock"
}

ensure_backend
ensure_node_folder apps/web "npm ci --no-audit --no-fund"
ensure_node_folder desktop "npm ci --ignore-scripts --no-audit --no-fund"

export PATH="/opt/venv/bin:$PATH"
export VIRTUAL_ENV=/opt/venv

exec bash -c "$1"
