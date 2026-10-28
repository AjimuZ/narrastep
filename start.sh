#!/bin/sh

set -eu

# Print the supported startup modes and their Docker Compose behavior.
print_usage() {
  cat <<'EOF'
Usage: ./start.sh [-r] [-h]

Start the complete Narrastep Docker Compose stack in the foreground.

Options:
  -r  Reuse existing images and never build them.
  -h  Show this help message.
EOF
}

reuse_images=false
show_help=false

while getopts ':rh' option; do
  case "$option" in
    r)
      reuse_images=true
      ;;
    h)
      show_help=true
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

if [ "$reuse_images" = true ]; then
  exec docker compose up --no-build
fi

exec docker compose up --build
