#!/bin/sh

set -eu

# Print the supported startup modes and their Podman Compose behavior.
print_usage() {
  cat <<'EOF'
Usage: ./start-podman.sh [-r] [--headless] [-h]

Start the complete Narrastep stack with rootless Podman in the foreground and, once it is
healthy, open Narrastep in its own desktop window. Compose runs through 'podman compose' or,
when that is missing, 'podman-compose'. The window needs Node.js 22.12 or newer with npm on
this computer; its first launch installs the pinned Electron into desktop/node_modules and
needs Internet access once. Closing the window never stops the stack; Ctrl+C here stops the
stack and closes the window.

Options:
  -r          Reuse existing images and never build them.
  --headless  Start only the Podman stack, for use with a normal browser (no window).
  -h, --help  Show this help message.
EOF
}

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
start_command=./start-podman.sh
compose_engine=podman

# Option parsing, the .env step and the desktop window watcher are shared with start.sh.
. "$script_directory/scripts/start-common.sh"

parse_start_options "$@"

cd "$script_directory"

if ! command -v podman >/dev/null 2>&1; then
  printf 'Podman is required. Install Podman (https://podman.io/docs/installation) and try again.\n' >&2
  exit 1
fi

# Choose the Compose provider: 'podman compose' first, then the standalone 'podman-compose'.
# 'podman compose' only forwards to a provider, so its version call also proves one is installed.
if podman compose version >/dev/null 2>&1; then
  compose_provider=podman-compose-plugin
elif command -v podman-compose >/dev/null 2>&1; then
  compose_provider=podman-compose-standalone
else
  printf 'A Compose provider for Podman is required, and none was found.\n' >&2
  printf 'Install docker-compose or podman-compose (for example: pip install podman-compose), then try again.\n' >&2
  exit 1
fi

# On macOS and Windows Podman runs in a virtual machine that this script never starts by itself.
case "$(uname -s 2>/dev/null || true)" in
  Darwin | MINGW* | MSYS* | CYGWIN*)
    if ! podman info >/dev/null 2>&1; then
      printf 'Podman does not answer. Start its virtual machine with: podman machine start\n' >&2
      printf '(run "podman machine init" first if none exists), then try again.\n' >&2
      exit 1
    fi
    ;;
esac

ensure_env_file

if [ "$headless" = false ]; then
  open_desktop_window
fi

if [ "$reuse_images" = true ]; then
  build_option=--no-build
else
  build_option=--build
fi

if [ "$compose_provider" = podman-compose-plugin ]; then
  exec podman compose up "$build_option"
fi

exec podman-compose up "$build_option"
