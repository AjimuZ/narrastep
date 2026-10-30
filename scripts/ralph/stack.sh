#!/usr/bin/env bash
# Runs `docker compose` on the real Compose file under an isolated project, ports and image tags.
#
# Usage: ./scripts/ralph/stack.sh up --build --detach --wait
#        ./scripts/ralph/stack.sh down --volumes
# Returns Compose's exit status, or 2 for a refused option.
# Guarantees: the owner's `narrastep` project, its data volume and the images `./start.sh -r` reuses
# are out of reach, and the owner's .env is not read. Recordings used here must be generated.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

for argument in "$@"; do
  case "$argument" in
    -p | --project-name | --project-name=* | -f | --file | --file=* | --env-file | --env-file=* | --project-directory | --project-directory=*)
      echo "stack: '$argument' is refused; this script fixes the project, files and settings" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"
compose=(
  docker compose
  --project-name narrastep-ralph
  --env-file scripts/ralph/stack.env
  --file docker-compose.yml
  --file scripts/ralph/stack.override.yml
)

# The agent can edit the Compose file, so what would start is checked first: no host folders except
# the read-only model import, no privileged containers and no Docker socket.
case "${1:-}" in
  up | run | create)
    "${compose[@]}" config --format json | node "$SCRIPT_DIR/prd.cjs" compose-check || exit 2
    ;;
esac

exec "${compose[@]}" "$@"
