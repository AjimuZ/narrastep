#!/bin/sh

set -eu

# Print the supported startup modes and their Docker Compose behavior.
print_usage() {
  cat <<'EOF'
Usage: ./start.sh [-r] [--headless] [-h]

Start the complete Narrastep Docker Compose stack in the foreground and, once it is healthy,
open Narrastep in its own desktop window. The window needs Node.js 22.12 or newer with npm on
this computer; its first launch installs the pinned Electron into desktop/node_modules and
needs Internet access once. Closing the window never stops the stack; Ctrl+C here stops the
stack and closes the window.

Options:
  -r          Reuse existing images and never build them.
  --headless  Start only the Docker stack, for use with a normal browser (no window).
  -h, --help  Show this help message.
EOF
}

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
start_command=./start.sh
compose_engine=docker

# Option parsing, the .env step and the desktop window watcher are shared with the other start script.
. "$script_directory/scripts/start-common.sh"

parse_start_options "$@"

cd "$script_directory"

if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose is required. Install Docker with the compose plugin and try again.\n' >&2
  exit 1
fi

ensure_env_file

if [ "$headless" = false ]; then
  open_desktop_window
fi

if [ "$reuse_images" = true ]; then
  exec docker compose up --no-build
fi

exec docker compose up --build
