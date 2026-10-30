#!/usr/bin/env bash
# Smoke test of start-podman.sh with real Podman, inside a Docker container, because Podman cannot
# be installed on the owner's computer. The `podman machine` path on Windows stays unverified.
#
# Usage: ./scripts/ralph/podman-smoke.sh
# Returns 0 when the stack became healthy under Podman from a generated .env and a full job passed,
# 3 when start-podman.sh does not exist yet, otherwise the failing step's status. The container is
# privileged because nested containers need it. That applies to this check only, and the product
# never asks for it. A copy of the repository is used, without .env, data/ or .git.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -f "$ROOT/start-podman.sh" ]; then
  echo "start-podman.sh does not exist yet"
  exit 3
fi

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
if command -v cygpath >/dev/null 2>&1; then
  HOST_ROOT="$(cygpath -m "$ROOT")"
else
  HOST_ROOT="$ROOT"
fi

# --cgroupns=host gives the nested runtime the cgroup controllers it needs under Docker Desktop.
exec docker run --rm --privileged --cgroupns=host \
  --label narrastep.ralph=sandbox \
  --volume "$HOST_ROOT:/source:ro" \
  --volume narrastep-ralph-podman:/var/lib/containers \
  debian:trixie-slim \
  bash -c '
    set -uo pipefail
    apt-get update -qq && apt-get install --yes -qq --no-install-recommends \
      podman podman-compose nftables aardvark-dns ca-certificates curl ffmpeg python3 fuse-overlayfs >/dev/null || exit 10
    podman version
    podman-compose version

    mkdir /work
    tar -C /source --exclude=.git --exclude=.env --exclude=data --exclude=.claude \
      --exclude=node_modules --exclude=.venv --exclude=.angular -cf - . | tar -C /work -xf -
    mkdir -p /work/data
    cd /work

    # The same small model as the other isolated runs, chosen before the generator writes .env.
    export NARRASTEP_WHISPER_MODEL=tiny
    ./start-podman.sh --headless > /tmp/start.log 2>&1 &
    waited=0
    until curl --silent --max-time 5 http://127.0.0.1:4200/api/v1/health | grep -q "\"status\":\"ok\""; do
      sleep 5
      waited=$((waited + 5))
      if [ "$waited" -ge 2400 ]; then
        echo "FAIL the stack did not become healthy in 40 minutes"
        tail -n 40 /tmp/start.log
        exit 11
      fi
    done
    echo "PASS the stack became healthy under Podman after $waited s"

    echo "--- generated .env, without values that could identify the computer"
    grep -E "^NARRASTEP_(WHISPER_MODEL|WHISPER_CPU_THREADS|WEB_PORT|API_PORT|MAX_UPLOAD_SIZE_MB)=" .env
    podman info --format "engine cpus={{.Host.CPUs}} memory={{.Host.MemTotal}}"

    if [ -f backend/scripts/full_job_check.py ]; then
      python3 backend/scripts/full_job_check.py --api http://127.0.0.1:8002/api/v1 || { echo "FAIL the full job"; exit 12; }
      echo "PASS the full job"
    else
      echo "FAIL backend/scripts/full_job_check.py is missing; P08.3 should have added it"
      exit 13
    fi
  '
