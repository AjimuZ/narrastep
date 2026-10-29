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

reuse_images=false
show_help=false
headless=false

# The leading ':' keeps getopts quiet and '-:' lets it deliver long options as OPTARG.
while getopts ':rh-:' option; do
  case "$option" in
    r)
      reuse_images=true
      ;;
    h)
      show_help=true
      ;;
    -)
      case "$OPTARG" in
        headless)
          headless=true
          ;;
        help)
          show_help=true
          ;;
        *)
          printf 'Unknown option: --%s\n' "$OPTARG" >&2
          print_usage >&2
          exit 2
          ;;
      esac
      ;;
    \?)
      printf 'Unknown option: -%s\n' "$OPTARG" >&2
      print_usage >&2
      exit 2
      ;;
  esac
done

shift "$((OPTIND - 1))"

if [ "$#" -ne 0 ]; then
  printf 'Unexpected argument: %s\n' "$1" >&2
  print_usage >&2
  exit 2
fi

if [ "$show_help" = true ]; then
  print_usage
  exit 0
fi

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_directory"

if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose is required. Install Docker with the compose plugin and try again.\n' >&2
  exit 1
fi

# Open the desktop window in the background once the stack answers its health check.
# The watcher runs next to Compose, which stays the foreground process of this script (the
# 'exec' below keeps this shell's PID), so Compose output and Ctrl+C behave as in headless mode.
# The watcher stops the launcher when Compose has exited, for example after a failed build or
# 'docker compose down' from another terminal; the launcher then closes an open window, so the
# window never outlives the stack. A failed window never stops the stack: the launcher explains
# the problem and the stack stays usable in a browser.
open_desktop_window() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    printf 'Narrastep: Node.js with npm was not found, so no desktop window will open.\n' >&2
    printf 'Narrastep: install Node.js 22.12 or newer, or use ./start.sh --headless with a browser.\n' >&2
    return 0
  fi

  compose_pid=$$
  (
    node "$script_directory/desktop/launch.cjs" &
    launcher_pid=$!
    while kill -0 "$launcher_pid" 2>/dev/null; do
      if ! kill -0 "$compose_pid" 2>/dev/null; then
        kill "$launcher_pid" 2>/dev/null || true
        break
      fi
      sleep 2
    done
  ) &
}

if [ "$headless" = false ]; then
  open_desktop_window
fi

if [ "$reuse_images" = true ]; then
  exec docker compose up --no-build
fi

exec docker compose up --build
