#!/bin/sh
# Prints a complete .env for this computer on standard output (ADR 0016); writes no file and makes no network request.
#
# Usage: scripts/generate-env.sh [--engine docker|podman]
#
# Contract:
# - Standard output is a valid .env: comment lines (date, measured values, the reason for each choice,
#   warnings) followed by KEY=VALUE lines. Exit status 0 on success, 2 on a bad argument.
# - A value that cannot be measured is reported as "unknown" and the default is used. Nothing is invented.
# - Each probe can be replaced for tests by a NARRASTEP_PROBE_* variable (set but empty means unknown):
#   HOST_CPUS, HOST_MEMORY_MB, ENGINE_CPUS, ENGINE_MEMORY_MB, FREE_DISK_MB, LISTENING_PORTS (space
#   separated), DATE. The profile functions below are pure: they read only their arguments.

# Defaults of the generated keys; they must equal docker-compose.yml and .env.example.
DEFAULT_MODEL=small
DEFAULT_UPLOAD_MB=4096
DEFAULT_MIN_FREE_DISK_MB=1024
DEFAULT_WEB_PORT=4200
DEFAULT_API_PORT=8002
PORT_STEPS=20

is_number() {
    case "$1" in
        '' | *[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

# min_known A B: the smaller of two measurements; unknown ones are ignored; prints "unknown" if both are.
min_known() {
    if is_number "$1" && is_number "$2"; then
        if [ "$1" -le "$2" ]; then echo "$1"; else echo "$2"; fi
    elif is_number "$1"; then
        echo "$1"
    elif is_number "$2"; then
        echo "$2"
    else
        echo unknown
    fi
}

# profile_model MEMORY_MB CPUS: tiny < 3 GB, base from 3 GB, small from 6 GB, medium from 12 GB with 6 CPUs.
profile_model() {
    if ! is_number "$1"; then
        echo "$DEFAULT_MODEL"
    elif [ "$1" -lt 3072 ]; then
        echo tiny
    elif [ "$1" -lt 6144 ]; then
        echo base
    elif [ "$1" -ge 12288 ] && is_number "$2" && [ "$2" -ge 6 ]; then
        echo medium
    else
        echo small
    fi
}

# profile_threads CPUS: all CPUs up to 3, otherwise CPUs minus one, at most 16; "unknown" when unmeasured.
profile_threads() {
    if ! is_number "$1" || [ "$1" -lt 1 ]; then
        echo unknown
    elif [ "$1" -le 3 ]; then
        echo "$1"
    elif [ "$1" -gt 17 ]; then
        echo 16
    else
        echo $(($1 - 1))
    fi
}

# profile_upload FREE_DISK_MB: the default, or a quarter of the free disk when smaller (at least 512 MB).
profile_upload() {
    if ! is_number "$1"; then
        echo "$DEFAULT_UPLOAD_MB"
        return
    fi
    quarter=$(($1 / 4))
    if [ "$quarter" -ge "$DEFAULT_UPLOAD_MB" ]; then
        echo "$DEFAULT_UPLOAD_MB"
    elif [ "$quarter" -lt 512 ]; then
        echo 512
    else
        echo "$quarter"
    fi
}

# profile_port DEFAULT TAKEN_PORTS: DEFAULT or the next port above it (at most PORT_STEPS steps) not in
# the space-separated TAKEN_PORTS; prints DEFAULT when every candidate is taken.
profile_port() {
    candidate=$1
    steps=0
    while [ "$steps" -le "$PORT_STEPS" ]; do
        case " $2 " in
            *" $candidate "*) ;;
            *)
                echo "$candidate"
                return
                ;;
        esac
        candidate=$((candidate + 1))
        steps=$((steps + 1))
    done
    echo "$1"
}

# --- probes (read the machine; the only impure part) ---

probe_override() {
    # probe_override NAME: prints the override value and succeeds when NARRASTEP_PROBE_NAME is set.
    eval "if [ -n \"\${NARRASTEP_PROBE_$1+x}\" ]; then printf '%s' \"\$NARRASTEP_PROBE_$1\"; return 0; fi"
    return 1
}

probe_host_cpus() {
    probe_override HOST_CPUS && return
    value=$(getconf _NPROCESSORS_ONLN 2>/dev/null) || value=
    if ! is_number "$value"; then value=$(nproc 2>/dev/null) || value=; fi
    if is_number "$value"; then printf '%s' "$value"; else printf unknown; fi
}

probe_host_memory_mb() {
    probe_override HOST_MEMORY_MB && return
    value=
    if [ -r /proc/meminfo ]; then
        kb=$(sed -n 's/^MemTotal:[[:space:]]*\([0-9][0-9]*\) kB.*/\1/p' /proc/meminfo 2>/dev/null)
        if is_number "$kb"; then value=$((kb / 1024)); fi
    fi
    if ! is_number "$value"; then
        bytes=$(sysctl -n hw.memsize 2>/dev/null) || bytes=
        if is_number "$bytes"; then value=$((bytes / 1048576)); fi
    fi
    if is_number "$value"; then printf '%s' "$value"; else printf unknown; fi
}

# probe_engine ENGINE FIELD: asks the local engine CLI (no network) for CPUs or MemTotal in bytes.
probe_engine() {
    case "$1" in
        docker) format="{{.$2}}" ;;
        podman) format="{{.Host.$2}}" ;;
        *)
            printf unknown
            return
            ;;
    esac
    value=$("$1" info --format "$format" 2>/dev/null) || value=
    if is_number "$value"; then printf '%s' "$value"; else printf unknown; fi
}

probe_engine_cpus() {
    probe_override ENGINE_CPUS && return
    if [ -z "$ENGINE" ]; then printf unknown; return; fi
    case "$ENGINE" in
        docker) probe_engine docker NCPU ;;
        podman) probe_engine podman CPUs ;;
    esac
}

probe_engine_memory_mb() {
    probe_override ENGINE_MEMORY_MB && return
    if [ -z "$ENGINE" ]; then printf unknown; return; fi
    case "$ENGINE" in
        docker) bytes=$(probe_engine docker MemTotal) ;;
        podman) bytes=$(probe_engine podman MemTotal) ;;
    esac
    if is_number "$bytes"; then printf '%s' $((bytes / 1048576)); else printf unknown; fi
}

probe_free_disk_mb() {
    probe_override FREE_DISK_MB && return
    root=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || root=.
    line=$(df -Pm "$root" 2>/dev/null | tail -n 1) || line=
    # shellcheck disable=SC2086
    set -- $line
    if is_number "$4"; then printf '%s' "$4"; else printf unknown; fi
}

# probe_listening_ports: TCP ports with a local listener, read from the socket table (no connection made).
probe_listening_ports() {
    probe_override LISTENING_PORTS && return
    if command -v ss >/dev/null 2>&1; then
        out=$(ss -ltn 2>/dev/null)
    elif command -v netstat >/dev/null 2>&1; then
        out=$(netstat -an 2>/dev/null)
    else
        return
    fi
    # In both "ss -ltn" and "netstat -an" the fourth field is the local address, ending in :PORT or .PORT.
    printf '%s\n' "$out" | while read -r line; do
        case "$line" in
            *LISTEN*)
                # shellcheck disable=SC2086
                set -- $line
                port=${4##*[:.]}
                if is_number "$port"; then printf '%s ' "$port"; fi
                ;;
        esac
    done
}

# --- main ---

ENGINE=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --engine)
            case "$2" in
                docker | podman) ENGINE=$2 ;;
                *)
                    echo "generate-env: --engine needs docker or podman" >&2
                    exit 2
                    ;;
            esac
            shift 2
            ;;
        *)
            echo "generate-env: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

# measured VALUE: VALUE when it is a number, otherwise "unknown" (also for an empty or invalid override).
measured() {
    if is_number "$1"; then printf '%s' "$1"; else printf unknown; fi
}

host_cpus=$(measured "$(probe_host_cpus)")
host_memory=$(measured "$(probe_host_memory_mb)")
engine_cpus=$(measured "$(probe_engine_cpus)")
engine_memory=$(measured "$(probe_engine_memory_mb)")
free_disk=$(measured "$(probe_free_disk_mb)")
listening=$(probe_listening_ports)

cpus=$(min_known "$host_cpus" "$engine_cpus")
memory=$(min_known "$host_memory" "$engine_memory")

model=$(profile_model "$memory" "$cpus")
threads=$(profile_threads "$cpus")
upload=$(profile_upload "$free_disk")
web_port=$(profile_port "$DEFAULT_WEB_PORT" "$listening")
api_port=$(profile_port "$DEFAULT_API_PORT" "$listening $web_port")
cors="http://127.0.0.1:$web_port,http://localhost:$web_port"

if [ -n "${NARRASTEP_PROBE_DATE+x}" ]; then date_text=$NARRASTEP_PROBE_DATE; else date_text=$(date -u +%Y-%m-%d); fi

echo "# Generated by scripts/generate-env.sh on $date_text. Delete this file to generate a new one."
echo "# Measured: CPUs host $host_cpus, engine $engine_cpus; memory host $host_memory MB, engine $engine_memory MB;"
echo "# free disk $free_disk MB. A value reported as unknown was not measured and its default is used."
echo "# Usable CPUs: $cpus. Usable memory: $memory MB."
echo "# NARRASTEP_WHISPER_MODEL=$model: tiny below 3 GB, base from 3 GB, small from 6 GB, medium from 12 GB with 6 CPUs."
if [ "$threads" = unknown ]; then
    echo "# NARRASTEP_WHISPER_CPU_THREADS is not set: the CPU count is unknown."
else
    echo "# NARRASTEP_WHISPER_CPU_THREADS=$threads: all CPUs up to 3, otherwise CPUs minus one, at most 16."
fi
echo "# NARRASTEP_MAX_UPLOAD_SIZE_MB=$upload: $DEFAULT_UPLOAD_MB MB, or a quarter of the free disk when smaller (at least 512)."
if [ "$web_port" != "$DEFAULT_WEB_PORT" ]; then
    echo "# NARRASTEP_WEB_PORT=$web_port: $DEFAULT_WEB_PORT is in use on 127.0.0.1."
fi
if [ "$api_port" != "$DEFAULT_API_PORT" ]; then
    echo "# NARRASTEP_API_PORT=$api_port: $DEFAULT_API_PORT is in use on 127.0.0.1."
fi
if is_number "$memory" && [ "$memory" -lt 6144 ]; then
    echo "# WARNING: only $memory MB of memory is usable; the 'small' model needs 6 GB (6144 MB)."
    echo "# Raise the limit in the Docker Desktop settings (Resources) or with 'podman machine set --memory 6144'."
    echo "# Nothing was changed on this computer."
fi
echo
echo "NARRASTEP_WHISPER_MODEL=$model"
echo "NARRASTEP_WHISPER_DEVICE=cpu"
echo "NARRASTEP_WHISPER_COMPUTE_TYPE=int8"
if [ "$threads" != unknown ]; then echo "NARRASTEP_WHISPER_CPU_THREADS=$threads"; fi
echo "NARRASTEP_MAX_UPLOAD_SIZE_MB=$upload"
echo "NARRASTEP_MIN_FREE_DISK_MB=$DEFAULT_MIN_FREE_DISK_MB"
echo "NARRASTEP_WEB_PORT=$web_port"
echo "NARRASTEP_API_PORT=$api_port"
echo "NARRASTEP_CORS_ORIGINS=$cors"
echo "NARRASTEP_ENV=development"
echo "NARRASTEP_LOG_LEVEL=INFO"
