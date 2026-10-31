# Shared logic of the start scripts: option parsing, the .env step, the model import folder and the
# desktop window watcher.
#
# Sourced with '.' by start.sh after it sets script_directory and defines print_usage and the
# variables below; it is never run on its own and does not change shell options.
#   start_command  how the user invokes the script, used in messages (for example ./start.sh)
#   compose_engine value of --engine passed to scripts/generate-env.sh (for example docker)

# Parse "$@" into reuse_images, headless and show_help, then print help or fail.
# Requires print_usage. Exits 2 on an unknown option or an unexpected argument, 0 after help.
parse_start_options() {
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
}

# Create .env from this computer's hardware when it is missing (ADR 0016). An existing .env, even an
# empty one, is never read, rewritten or replaced. The generator writes to a temporary file that is
# moved into place only on success, so a failed run leaves no partial .env for the next start to trust.
# This runs before the desktop watcher so the launcher reads the generated NARRASTEP_WEB_PORT.
# Requires the current directory to be the project root. Exits 1 when generation fails.
ensure_env_file() {
  if [ ! -e .env ] && [ ! -L .env ]; then
    generated_env=".env.generating.$$"
    if ! sh "$script_directory/scripts/generate-env.sh" --engine "$compose_engine" >"$generated_env" 2>/dev/null ||
      ! mv "$generated_env" .env 2>/dev/null; then
      rm -f "$generated_env"
      printf 'Narrastep: could not generate .env from this computer. Nothing was started.\n' >&2
      printf 'Narrastep: copy .env.example to .env and try again.\n' >&2
      exit 1
    fi
    printf 'Narrastep: created .env for this computer. Delete it to generate a new one.\n'
  fi
}

# Create the folder the model import reads from, so the bind mount of docker-compose.yml never
# points at a missing path: Docker would create it owned by root and Podman would refuse to start.
# The folder is NARRASTEP_MODEL_IMPORT_DIR from the environment or from .env, else data/models-import.
# Requires the current directory to be the project root. Never exits: a folder that cannot be
# created only prints a warning, because the import is optional and the rest of the stack is not.
ensure_model_import_folder() {
  import_folder=${NARRASTEP_MODEL_IMPORT_DIR:-}
  if [ -z "$import_folder" ] && [ -r .env ]; then
    # Built-ins only, so a missing text tool never stops the start; the last assignment wins, as
    # Compose reads it.
    carriage_return=$(printf '\r')
    while IFS= read -r env_line || [ -n "$env_line" ]; do
      case "$env_line" in
        NARRASTEP_MODEL_IMPORT_DIR=*)
          import_folder=${env_line#NARRASTEP_MODEL_IMPORT_DIR=}
          import_folder=${import_folder%"$carriage_return"}
          case "$import_folder" in
            \"*\") import_folder=${import_folder#\"}; import_folder=${import_folder%\"} ;;
            \'*\') import_folder=${import_folder#\'}; import_folder=${import_folder%\'} ;;
          esac
          ;;
      esac
    done < .env
  fi
  if [ -z "$import_folder" ]; then
    import_folder=data/models-import
  fi
  if ! mkdir -p -- "$import_folder" 2>/dev/null; then
    printf 'Narrastep: could not create the model import folder %s; importing models from disk may not work.\n' "$import_folder" >&2
  fi
}

# Open the desktop window in the background once the stack answers its health check.
# The watcher runs next to Compose, which stays the foreground process of the script (its final
# 'exec' keeps the shell's PID), so Compose output and Ctrl+C behave as in headless mode.
# The watcher stops the launcher when Compose has exited, for example after a failed build or
# 'docker compose down' from another terminal; the launcher then closes an open window, so the
# window never outlives the stack. A failed window never stops the stack: the launcher explains
# the problem and the stack stays usable in a browser.
open_desktop_window() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    printf 'Narrastep: Node.js with npm was not found, so no desktop window will open.\n' >&2
    printf 'Narrastep: install Node.js 22.12 or newer, or use %s --headless with a browser.\n' "$start_command" >&2
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
