#!/usr/bin/env bash
# Opens a run in its own Git Bash window, which keeps going after this terminal or VS Code closes.
#
# Usage: ./scripts/ralph/open-run.sh [arguments of ralph.sh]
#        ./scripts/ralph/open-run.sh --resume 60
# Returns 0 once the window is requested; the run's own result stays visible in that window and in
# scripts/ralph/.state/run.log. Clears Claude Code's variables, so that the run is independent of
# any session this script was started from. Windows with Git for Windows only.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="$SCRIPT_DIR/.state"
GIT_BASH="${RALPH_GIT_BASH:-C:/Program Files/Git/git-bash.exe}"
WINDOW_SCRIPT="$STATE_DIR/open-run-window.sh"

if [ ! -e "$GIT_BASH" ]; then
  echo "open-run: Git Bash not found at $GIT_BASH; set RALPH_GIT_BASH, or run ralph.sh in a terminal" >&2
  exit 2
fi
for argument in "$@"; do
  [[ "$argument" =~ ^(--resume|--dry-run|[0-9]+)$ ]] || { echo "open-run: unknown argument '$argument'" >&2; exit 2; }
done

# The command goes through a file, because the quoting rules of Windows would break it on the way.
mkdir -p "$STATE_DIR"
cat > "$WINDOW_SCRIPT" <<EOF
#!/usr/bin/env bash
# Inherited from the call below; with it, Git Bash would pass Node.js paths that do not exist.
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
cd '$ROOT' || exit 2
./scripts/ralph/ralph.sh $*
status=\$?
echo
echo "The run ended with status \$status. Press Enter to close this window."
read -r _
EOF

while IFS='=' read -r name _; do
  case "$name" in
    CLAUDE* | RALPH_ALLOW_NESTED) unset "$name" ;;
  esac
done < <(env)

# Start-Process detaches the window, so it outlives this script and holds none of its pipes.
MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
  "Start-Process -FilePath '$(cygpath -w "$GIT_BASH")' -ArgumentList '\"$(cygpath -m "$WINDOW_SCRIPT")\"'"
echo "The run was opened in a separate window. Follow it with ./scripts/ralph/status.sh --watch"
