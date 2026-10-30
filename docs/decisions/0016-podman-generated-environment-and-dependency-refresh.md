# ADR 0016: Podman launcher, generated environment file and dependency refresh

Status: accepted (2026-10-29), planned in roadmap package P08; nothing below is implemented yet.
Supersedes: none. Refines: the "Docker is required" constraint in [Requirements](../REQUIREMENTS.md).

## Context

The owner asked for three connected changes (request recorded on 2026-10-29, in Italian):

1. Narrastep must also run with **Podman**, through a dedicated start script.
2. When the project starts and there is **no `.env` file**, one must be generated with the best
   configuration for the computer it runs on.
3. **Every library must be updated to the latest possible version.** Transcription sometimes
   fails while the audio is extracted because Python libraries are incompatible with each other;
   this must be checked and fixed.

What exists today (Version 0.2, checked in the repository on 2026-10-29):

- `start.sh` calls `docker compose` only. It reads `NARRASTEP_WEB_PORT` from `.env` for the desktop
  window, but nothing creates `.env`; `.env.example` is copied by hand and is Git-ignored once
  copied. Compose falls back to the defaults written in `docker-compose.yml`, so the stack runs
  without `.env`, but with one fixed profile (model `small`, port 4200, 4 GB upload limit) for every
  computer.
- `backend/pyproject.toml` pins only the direct dependencies. `backend/Dockerfile` runs
  `pip install .`, so the transitive dependencies of `faster-whisper` (PyAV, CTranslate2,
  ONNX Runtime, `tokenizers`, `huggingface_hub`) are resolved to whatever is newest on the day of
  the build. The audio itself is extracted with the system FFmpeg of the Debian base image
  (`extract_pcm_audio` in `backend/app/services/media.py`), also unpinned. Builds are therefore not
  reproducible and an incompatible release of a transitive package or of FFmpeg can break
  transcription without any change in the repository. This is a hypothesis: the failure has not
  been reproduced in the repository yet, and P08.1 must establish the real cause first.
  *Finding of P08.1 (2026-10-29):* reproduced. The transitive PyAV, not FFmpeg, is at fault:
  faster-whisper 1.2.1 decodes a file path with PyAV and passes `metadata_errors` to `av.open`,
  which PyAV 19.0.0 (released 2026-09-29) removed, so every transcription in an image built since
  then fails with `TypeError`; PyAV 18.1.0 works. Narrastep now passes Whisper the samples of the
  WAV that FFmpeg extracted, so PyAV is no longer used at runtime, and the image build runs
  `scripts/check_transcription_stack.py`. Evidence is in the roadmap entry for P08.1.
- `docker-compose.yml` and the Dockerfiles use unqualified image names (`redis:7.2.14-alpine`,
  `python:3.12.13-slim`, `node:22.23.1-alpine`, `nginx:1.28.0-alpine`). Podman without a configured
  search registry asks interactively or fails for such names.
- The backend accepts only `cpu` and `int8` for Whisper (`backend/app/config.py`); GPU use is a
  "Later" item and stays out of scope here.

## Decision

### Podman is a second supported container engine

- Docker stays the default and keeps `./start.sh` unchanged for users. Podman is added with a
  **separate script, `start-podman.sh`**, with the same options (`-r`, `--headless`, `-h`) and the
  same desktop-window behavior. A user needs one engine, not both.
- The Podman script runs **rootless**, never uses `sudo` or `--privileged` and never changes
  Podman's configuration. It prefers `podman compose` and falls back to `podman-compose`; it
  explains what to install when neither exists. On macOS and Windows it checks that `podman
  machine` is running and tells the user how to start it; it does not start it silently.
- The Compose file and the Dockerfiles become engine-neutral, so one definition serves both
  engines: fully qualified image names (`docker.io/library/...`), health checks declared in
  Compose rather than only in a Dockerfile `HEALTHCHECK` (Podman's default OCI image format drops
  that instruction), no Docker-only options and no bind mounts (so no SELinux relabeling).
  Containers keep loopback-only published ports, the internal `processing` network and named
  volumes. *One planned exception (2026-10-29, Q5):* P06.2 adds a read-only bind mount of the
  model import folder on the `api` service ([Local generation](../LOCAL_GENERATION.md#import-from-disk)).
  The start scripts create the folder first and the Compose entry adds the SELinux relabel option,
  which P08.9 must verify on Podman.
- Shared logic (option parsing, the watcher that opens the window, the `.env` step) moves into one
  sourced file under `scripts/`, so `start.sh` and `start-podman.sh` cannot drift apart. The
  behavior of `./start.sh` and its tests (`desktop/test/start-script.test.cjs`) must not change.
- The desktop window is unaffected: it talks to `http://127.0.0.1:{NARRASTEP_WEB_PORT}` and never
  calls an engine ([ADR 0014](0014-desktop-app-shell.md)).
- This relaxes the product constraint "Docker is required" to "Docker or Podman is required". The
  other half of the constraint is unchanged: no other software is installed on the computer, and
  host Node.js with npm is used only to open the window. The owner's earlier instruction to ignore
  Podman during the P07.2 smoke test (2026-10-29) still applies to that test only; it is superseded
  for this package.

### A missing `.env` is generated from the computer

- Both start scripts run the same generator **only when `.env` does not exist**. An existing
  `.env`, even an empty one, is never read for rewriting, merged or overwritten. The file is
  written to a temporary name in the project folder and renamed, with owner-only permissions where
  the system supports them, so an interrupted run leaves no half-written file.
- The generator is a POSIX shell script, because the host may have nothing but a shell, and it
  needs no network access. It prints a header with the generation date, the measured values and
  the reason for each choice, then the keys of `.env.example`. `.env` stays Git-ignored.
- **Measured values.** The generator asks the container engine for what it can really use
  (`docker info` or `podman info`: CPU count and total memory), because Docker Desktop and a Podman
  machine limit memory below the host's, and takes the smaller of the engine and host values. Host
  values come from `getconf _NPROCESSORS_ONLN` or `nproc`, `/proc/meminfo` or `sysctl hw.memsize`.
  Free disk comes from `df` on the project folder. A value that cannot be measured is reported as
  unknown and the default of `.env.example` is used for it. Nothing is invented and nothing is sent
  anywhere.
- **Initial profile**, deterministic and covered by tests with fixed hardware. P08.6 may refine
  the thresholds with measured evidence and must then update this table:

| Key | Rule |
| --- | --- |
| `NARRASTEP_WHISPER_MODEL` | `tiny` below 3 GB of usable memory, `base` from 3 GB, `small` from 6 GB, `medium` from 12 GB with at least 6 CPUs. The model is baked into the image at build time, so the choice decides what the first build downloads |
| `NARRASTEP_WHISPER_DEVICE`, `NARRASTEP_WHISPER_COMPUTE_TYPE` | Always `cpu` and `int8`; the backend accepts nothing else and CPU support must not change |
| `NARRASTEP_WHISPER_CPU_THREADS` (new, backend setting) | All CPUs up to 3, otherwise CPUs minus one, at most 16, so the interface stays responsive during transcription |
| `NARRASTEP_MAX_UPLOAD_SIZE_MB`, `NARRASTEP_MIN_FREE_DISK_MB` | Never above what the free disk can hold: the upload limit is the default 4096 MB, lowered to a quarter of the free disk when that is smaller (at least 512 MB), and the free-disk guard keeps its default |
| `NARRASTEP_WEB_PORT`, `NARRASTEP_API_PORT` | The default 4200 and 8002, or the next free port above (at most 20 steps) when the default is in use on `127.0.0.1`; the choice is written in the header |
| `NARRASTEP_CORS_ORIGINS` | Rebuilt from the chosen web port, so a changed port never breaks the API |
| All other keys | The values of `.env.example` |

- The Celery worker stays at `--concurrency=1`: one job at a time is an architecture rule
  ([ADR 0003](0003-celery-and-redis.md)), not a tuning knob.
- When the engine's memory is below the 6 GB that `small` needs, the script prints how to raise the
  limit (Docker Desktop settings, `podman machine set --memory`) and does not change the machine.
- The generator never touches the user's Compose file or the engine's configuration, makes no
  network request and records no hardware data outside `.env` (rule 11 in `CLAUDE.md`).

### Dependencies are updated to the latest working versions, with the transcription fault fixed first

- **Diagnose before upgrading.** P08.1 reproduces the audio-extraction failure with real media
  variants (silent video, mono and multichannel audio, unusual sample rates, long files, each
  container Narrastep accepts) and adds a **transcription stack check** that runs inside the image
  build, in the style of `check_llama_server.py` (risk K6): it extracts audio from a generated clip
  with the image's FFmpeg and transcribes it with the baked model, and fails the build otherwise.
- **Lock everything.** The backend gets a fully pinned lock of direct and transitive
  packages that the Dockerfile installs, so the same sources always build the same image. Updates
  are deliberate, one commit per component, never silent.
  *Done in P08.2 (2026-10-29):* `backend/requirements.lock` (runtime packages, pip and the build
  backend) and `backend/requirements-dev.lock` (plus the development tools) are generated by `uv pip
  compile --universal --generate-hashes` from the `==` pins in `backend/pyproject.toml`. The image
  installs the lock with `--require-hashes --no-deps --only-binary=:all:`, then the project with
  `--no-deps --no-build-isolation --no-index`, and runs `pip check`; CI does the same with the
  development lock. A lock file in pip's requirements format was chosen over `pylock.toml` and
  `uv.lock` because the image's own pip installs it without another tool; `uv` is a pinned
  development tool used only to regenerate the files. The backend moved to Python 3.14.10, the
  newest release with wheels for every locked package. Two components stay behind, with the reasons
  in [Licenses](../LICENSES.md#kept-behind-their-newest-release): Python 3.15 (no ONNX Runtime
  wheels) and redis-py 8 (Kombu 5.6.2 accepts below 6.5). FastAPI 0.142 and later bring
  OpenTelemetry instrumentation through `opentelemetry-api`; Narrastep switches it off in
  `create_app` and installs no SDK or exporter, so the no-telemetry rule holds.
- **Latest means latest that passes.** For each component the newest stable release whose license
  is allowed by [Licenses](../LICENSES.md) and whose full test suite and build pass is adopted.
  A component kept behind its latest release must say why, with the evidence, in
  [Licenses](../LICENSES.md) or [Risks](../RISKS.md). Components in scope: Python and the backend
  packages, the Debian base image with its FFmpeg and Tesseract, the pinned `llama.cpp` server,
  Redis (or Valkey, see risk K9), nginx, Node.js and the Angular application's packages, Electron
  and the desktop shell's packages, the GitHub Actions in `.github/workflows/ci.yml` and the
  development tools (ruff, mypy, pytest, Playwright, Prettier).
- Redis is not upgraded blindly: version 7.4 and later left the BSD license (risk K9). The owner's
  rule is free licenses only, so the task compares the options and records the choice.
  *Decided on 2026-10-29 (Q10):* the queue server moves from Redis 7.2.14 to **Valkey** under
  BSD-3-Clause. Redis 7.2 is the last BSD release and receives no further releases, and Redis 8 is
  under AGPL or RSAL, which the free-license rule avoids; Valkey keeps the Redis protocol, so Celery,
  Kombu and redis-py 6.4.0 stay as they are (P08.2). [ADR 0017](0017-valkey-broker.md) records the
  result: Valkey 9.1.3, keeping the `redis` service name, URLs and volume. The role of the server in
  ADR 0003 (temporary coordination, never persistent truth) does not change. P08.3 stayed open
  until Docker ran the healthy stack and a full job on 2026-10-30.
- The upgrade is verified by tests, a real image build and a real transcription, never by version
  numbers alone. A check that this worker cannot run (no container engine) is reported as not run
  and the task stays unchecked.

## Alternatives

- **One `start.sh` that detects Podman or Docker.** Simpler for the user, but the owner asked for a
  dedicated script, and automatic detection would pick an engine silently on computers that have
  both. A shared library keeps the two scripts consistent without that risk.
- **Docker-compatible Podman socket plus unchanged `docker compose`.** Needs system configuration
  (a user service and `DOCKER_HOST`) that the script must not change, and hides which engine runs.
- **Generate `.env` with Python or Node.js.** Python is not available on the host by rule; Node.js
  is only expected for the window. POSIX shell is the only runtime `start.sh` can rely on.
- **Rewrite or merge an existing `.env`.** Risks overwriting a user's choices; the user can delete
  the file to get a new one.
- **Use the GPU when present.** Out of scope: the backend accepts only CPU and `int8`, and GPU images
  are large (listed under "Later" in the [roadmap](../ROADMAP.md)).
- **Float all dependencies to the newest versions at build time.** This is the present behavior
  and the probable source of the Whisper errors; reproducible builds are preferred.

## Consequences

- New or changed files (planned): `start-podman.sh`, a sourced helper and the generator under
  `scripts/`, `docker-compose.yml`, `backend/Dockerfile`, `apps/web/Dockerfile`,
  `backend/pyproject.toml` with a lock file, `backend/app/config.py` (`whisper_cpu_threads`),
  `.env.example`, `.github/workflows/ci.yml`, the package manifests of `apps/web` and `desktop`.
- Documentation to update when the work lands: [Requirements](../REQUIREMENTS.md) (R16–R18 and the
  host-install constraint), [Architecture](../ARCHITECTURE.md), [Development](../DEVELOPMENT.md),
  [Licenses](../LICENSES.md), `README.md`, `CONTRIBUTING.md`, `CLAUDE.md` rule 13 and the
  changelog. `docs/API.md` and `docs/openapi.json` change only if the update alters endpoints.
- New dependencies are recorded in [Licenses](../LICENSES.md) before they are merged (rule 17).
- Risks K15–K17 in [Risks](../RISKS.md) track an unreproducible transcription fault, Podman
  differences and a small Podman machine.
- Evidence required: shell tests with a stubbed engine for both scripts, generator tests with fixed
  hardware, the transcription stack check passing in a freshly built image, the full backend,
  frontend and desktop suites, and an owner-run smoke test on a real Podman installation (P08.12).
