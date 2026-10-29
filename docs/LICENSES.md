# Licenses

Narrastep is published under the [MIT License](../LICENSE) as a free, non-commercial public
project. Every dependency, model, font, tool and service it uses must be available at no cost
under a free or open-source license that allows public redistribution of this repository. Paid
licenses, paid services and paid certificates are not used.

Licenses below are those declared by each upstream project, checked on 2026-10-28; the backend
Python packages and their licenses were checked again on 2026-10-29 against the installed package
metadata of the lock (P08.2), and the container images, their digests and the Debian package
versions on 2026-10-29 against the registries and the Debian 13 package index (P08.3). An automated
inventory inside the containers is roadmap item I-15.

## Runtime components (built locally, not redistributed as images)

| Component | Version | License | Note |
| --- | --- | --- | --- |
| Python | 3.14.10 | PSF-2.0 | Base image `python:3.14.10-slim-trixie` (Debian 13.7), pinned by [digest](#container-images); see [why not 3.15](#kept-behind-their-newest-release) |
| FastAPI, Pydantic, pydantic-settings | 0.162.0, 2.14.2, 2.16.2 | MIT | FastAPI's OpenTelemetry instrumentation is [switched off](#fastapi-and-opentelemetry) |
| Uvicorn | 0.54.0 | BSD-3-Clause | |
| SQLAlchemy, Alembic | 2.1.6, 1.20.1 | MIT | |
| Celery | 5.6.3 | BSD-3-Clause | |
| redis-py | 6.4.0 | MIT | [Kept behind](#kept-behind-their-newest-release) 8.1.0 |
| Valkey server | 9.1.3 | BSD-3-Clause | Redis-protocol broker since P08.3 ([ADR 0017](decisions/0017-valkey-broker.md)); replaced Redis 7.2.14, because Redis 7.4 and later are not under a free license |
| SQLite | bundled with Python | Public domain | |
| faster-whisper, CTranslate2 | 1.2.1, 4.8.4 | MIT | |
| Whisper model weights | `small` | MIT | Downloaded at image build |
| OpenCV (headless) | 5.0.0.93 | Apache-2.0 | |
| NumPy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Bundled libraries |
| Pillow | 13.0.0 | MIT-CMU | |
| python-multipart | 0.0.32 | Apache-2.0 | |
| swagger-ui-bundle | 1.1.0 | Apache-2.0 | Serves API docs locally, no CDN |
| FFmpeg (Debian package) | 7.1.5-0+deb13u1 (Debian 13) | GPL-2.0-or-later build | Built locally; if images are ever published, GPL source obligations apply. The image-build transcription check (P08.1) speaks its test sentence with FFmpeg's `flite` source; `libflite1` (BSD-style Flite license) is already installed as an FFmpeg dependency, so nothing is added |
| Tesseract and language data | 5.5.0-1+b1, data 4.1.0-2 (Debian 13) | Apache-2.0 | |
| `llama.cpp` server (CPU) | v0.5.0, build b11146 | MIT | Copied into `/opt/llama.cpp` from `ghcr.io/ggml-org/llama.cpp:server-v0.5.0` pinned by digest (P06.1); license text in `backend/third_party/llama.cpp/LICENSE`, copied beside the binaries |
| OpenMP runtime `libgomp1` (Debian package) | 14.2.0-19 (Debian 13) | GPL-3.0-or-later with the GCC Runtime Library Exception | Needed by `llama.cpp`; already installed for FFmpeg and Tesseract |
| Nginx | 1.30.6 | BSD-2-Clause | Newest stable line |
| Node.js (build only) | 22.23.1 | MIT | [Kept behind](#container-images) 26.13.0 until P08.4 |
| Angular, Angular CDK | 22.0.x | MIT | |
| `@angular/localize` | 22.0.6 | MIT | Runtime `$localize` and `loadTranslations`; its Babel tooling (`@babel/core`, MIT) is a build-time dependency of the Angular tools and is not shipped in the web image |
| RxJS | 7.8.2 | Apache-2.0 | |
| zone.js, tslib | 0.16.2, 2.8.1 | MIT, 0BSD | |

The Debian package versions are those the backend image build resolved against the Debian 13
(`trixie`, `trixie-updates`, `trixie-security`) index on 2026-10-29. The Dockerfile does not pin
them: Debian removes superseded versions from its archive, so an exact pin would stop later builds.
A security update can therefore change them between builds; the image build's transcription and
`llama.cpp` checks fail when an update breaks either.

## Container images

Every image the stack builds from or pulls has an exact version tag and the digest of its
multi-platform index (amd64 and arm64 included), checked against the registry on 2026-10-29
(P08.3). `backend/tests/test_container_images.py` fails when this table and the Dockerfiles or
`docker-compose.yml` differ, or when an image has a floating tag or no digest.

| Image | Tag | Index digest | Used for | License |
| --- | --- | --- | --- | --- |
| `ghcr.io/ggml-org/llama.cpp` | `server-v0.5.0` | `sha256:a94b642b3e2620749bf2ff5672df922c23a6202710e54a8f7667e58df70aba5a` | `llama-server` and its libraries, copied into the backend image | MIT |
| `python` | `3.14.10-slim-trixie` | `sha256:3cabfd7d7aaf394e6040e79fa1eec6ca1c52b74ed9188650472695d9a9326999` | Backend runtime base (Debian 13.7) | PSF-2.0; Debian packages under their own licenses |
| `node` | `22.23.1-alpine` | `sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2` | Web build stage only | MIT |
| `nginx` | `1.30.6-alpine` | `sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949` | Web server | BSD-2-Clause |
| `valkey/valkey` | `9.1.3-alpine` | `sha256:bf09d58e96d6b4524a15396b65b242bbe18beebbcc15a04d8ee75ddac62fae0b` | Redis-protocol broker (`redis` service) | BSD-3-Clause |

Kept behind or unchanged, with the reason:

| Component | Pinned | Newest | Reason |
| --- | --- | --- | --- |
| Node.js | 22.23.1 | 26.13.0 (current LTS); 22.23.4 in the 22 line | `apps/web/package.json` requires exactly `.nvmrc`'s version and `.npmrc` sets `engine-strict`, so the image, `.nvmrc`, `engines.node`, the lock and CI move together in P08.4 |
| `llama.cpp` server | `server-v0.5.0` (build b11146) | `server-v0.5.0`; rolling builds up to `server-b11977` | Newest versioned release; the `server-b*` tags are per-commit builds without a release, which [Local generation](LOCAL_GENERATION.md#server-in-the-backend-image) does not adopt |
| Python | 3.14.10 | 3.15.2 | No ONNX Runtime wheels for 3.15 ([below](#kept-behind-their-newest-release)) |
| FFmpeg, Tesseract, `libgomp1` | Debian 13 versions above | FFmpeg 9.0.2, Tesseract 5.5.0-1.1, `libgomp1` 16.2.0-3 in Debian testing (`forky`) | The base stays on the Debian stable release; no Python image is published for Debian testing and `trixie-backports` carries none of them |
| Redis server | — | 8.x | Not used: Redis 7.4 and later are under RSALv2 or SSPLv1 (and, since 8.0, AGPLv3); the [rules](#rules-for-new-dependencies) forbid the first two and allow the GPL family only for programs called as subprocesses, while the broker is a network service. Valkey replaces it ([ADR 0017](decisions/0017-valkey-broker.md)) |

## Backend Python packages

`backend/requirements.lock` pins every package the backend image installs, direct and transitive,
with its SHA-256 hashes, and the image installs nothing else (P08.2,
[Backend package lock](DEVELOPMENT.md#backend-package-lock)). `tests/test_dependencies.py` fails when
a locked package is missing from this table or has another version here. "Direct" means the
package is pinned in `backend/pyproject.toml`; the others are pulled in by the packages named.

| Package | Version | License | Required by | Note |
| --- | --- | --- | --- | --- |
| `alembic` | 1.20.1 | MIT | direct |  |
| `amqp` | 5.4.1 | BSD-3-Clause | `kombu` |  |
| `annotated-doc` | 0.0.5 | MIT | `fastapi` |  |
| `annotated-types` | 0.8.0 | MIT | `pydantic` |  |
| `anyio` | 4.15.1 | MIT | `httpx2`, `starlette`, `watchfiles` |  |
| `av` | 19.0.2 | BSD-3-Clause | `faster-whisper` | Installed for faster-whisper; not used at run time since P08.1 |
| `billiard` | 4.3.1 | BSD-3-Clause | `celery` |  |
| `celery` | 5.6.3 | BSD-3-Clause | direct |  |
| `click` | 8.5.0 | BSD-3-Clause | `celery`, `click-didyoumean`, `click-plugins`, `click-repl`, `huggingface-hub`, `uvicorn` |  |
| `click-didyoumean` | 0.3.1 | MIT | `celery` |  |
| `click-plugins` | 1.1.1.2 | BSD-3-Clause | `celery` |  |
| `click-repl` | 0.4.1 | MIT | `celery` |  |
| `colorama` | 0.4.6 | BSD-3-Clause | `tqdm` | Windows only |
| `ctranslate2` | 4.8.4 | MIT | `faster-whisper` |  |
| `fastapi` | 0.162.0 | MIT | direct |  |
| `faster-whisper` | 1.2.1 | MIT | direct |  |
| `filelock` | 4.2.4 | MIT | `huggingface-hub` |  |
| `flatbuffers` | 25.12.19 | Apache-2.0 | `onnxruntime` |  |
| `fsspec` | 2026.9.0 | BSD-3-Clause | `huggingface-hub` |  |
| `h11` | 0.16.0 | MIT | `httpcore2`, `uvicorn` |  |
| `hatchling` | 1.33.0 | MIT | direct | `build` group; the build backend |
| `hf-xet` | 1.7.1 | Apache-2.0 | `huggingface-hub` | 64-bit x86 and Arm only |
| `httpcore2` | 2.16.0 | BSD-3-Clause | `httpx2` | Not on Emscripten |
| `httptools` | 0.9.0 | MIT | `uvicorn` |  |
| `httpx2` | 2.16.0 | BSD-3-Clause | `huggingface-hub` |  |
| `httpx2-jsfetch` | 1.0 | BSD-3-Clause | `httpx2` | Emscripten only; never installed by Narrastep |
| `huggingface-hub` | 2.4.0 | Apache-2.0 | `faster-whisper`, `tokenizers` |  |
| `idna` | 3.20 | BSD-3-Clause | `anyio`, `httpx2` |  |
| `jinja2` | 3.1.6 | BSD-3-Clause | `swagger-ui-bundle` |  |
| `kombu` | 5.6.2 | BSD-3-Clause | `celery` |  |
| `mako` | 1.4.3 | MIT | `alembic` |  |
| `markupsafe` | 3.0.4 | BSD-3-Clause | `jinja2`, `mako` |  |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | direct |  |
| `onnxruntime` | 1.31.1 | MIT | `faster-whisper` |  |
| `opencv-python-headless` | 5.0.0.93 | Apache-2.0 | direct |  |
| `opentelemetry-api` | 1.45.1 | Apache-2.0 | `fastapi` | Required by FastAPI; its instrumentation is switched off, see below |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | `hatchling`, `huggingface-hub`, `kombu`, `onnxruntime` |  |
| `pathspec` | 1.1.1 | MPL-2.0 | `hatchling` |  |
| `pillow` | 13.0.0 | MIT-CMU | direct |  |
| `pip` | 26.2.2 | MIT | direct | `build` group |
| `pluggy` | 1.7.0 | MIT | `hatchling` |  |
| `prompt-toolkit` | 3.0.53 | BSD-3-Clause | `click-repl` |  |
| `protobuf` | 7.36.2 | BSD-3-Clause | `onnxruntime` |  |
| `pydantic` | 2.14.2 | MIT | direct |  |
| `pydantic-core` | 2.50.2 | MIT | `pydantic` |  |
| `pydantic-settings` | 2.16.2 | MIT | direct |  |
| `python-dateutil` | 3.0.0 | Apache-2.0 AND BSD-3-Clause | `celery` |  |
| `python-dotenv` | 1.2.4 | BSD-3-Clause | `pydantic-settings`, `uvicorn` |  |
| `python-multipart` | 0.0.32 | Apache-2.0 | direct |  |
| `pyyaml` | 6.0.3 | MIT | `ctranslate2`, `huggingface-hub`, `uvicorn` |  |
| `redis` | 6.4.0 | MIT | direct | Kept behind 8.1.0, see below |
| `six` | 1.17.0 | MIT | `python-dateutil` |  |
| `sqlalchemy` | 2.1.6 | MIT | direct |  |
| `starlette` | 1.10.0 | BSD-3-Clause | `fastapi` |  |
| `swagger-ui-bundle` | 1.1.0 | Apache-2.0 | direct |  |
| `tokenizers` | 0.23.3 | Apache-2.0 | `faster-whisper` |  |
| `tomlkit` | 0.15.2 | MIT | `hatchling` |  |
| `tqdm` | 4.70.1 | MPL-2.0 AND MIT | `faster-whisper`, `huggingface-hub` |  |
| `trove-classifiers` | 2026.9.21.13 | Apache-2.0 | `hatchling` |  |
| `truststore` | 0.10.5 | MIT | `httpcore2`, `httpx2` | Not on Emscripten |
| `typing-extensions` | 4.16.0 | PSF-2.0 | `alembic`, `anyio`, `click-repl`, `fastapi`, `huggingface-hub`, `opentelemetry-api`, `pydantic`, `pydantic-core`, `pydantic-settings`, `sqlalchemy`, `typing-inspection` |  |
| `typing-inspection` | 0.4.4 | MIT | `fastapi`, `pydantic`, `pydantic-settings` |  |
| `tzdata` | 2026.6 | Apache-2.0 | `kombu`, `python-dateutil`, `tzlocal` |  |
| `tzlocal` | 5.4.5 | MIT | `celery` |  |
| `uvicorn` | 0.54.0 | BSD-3-Clause | direct |  |
| `uvloop` | 0.23.0 | MIT OR Apache-2.0 | `uvicorn` | Not on Windows, Cygwin or PyPy |
| `vine` | 5.1.0 | BSD-3-Clause | `amqp`, `celery`, `kombu` |  |
| `watchfiles` | 1.3.0 | MIT | `uvicorn` |  |
| `wcwidth` | 0.9.4 | MIT | `prompt-toolkit` |  |
| `websockets` | 17.3 | BSD-3-Clause | `uvicorn` |  |

`backend/requirements-dev.lock` adds these development tools, never installed in the image:

| Package | Version | License | Required by | Note |
| --- | --- | --- | --- | --- |
| `ast-serialize` | 0.13.1 | MIT | `mypy` |  |
| `certifi` | 2026.10.23 | MPL-2.0 | `httpcore`, `httpx` |  |
| `coverage` | 7.17.0 | Apache-2.0 | `pytest-cov` |  |
| `editables` | 0.6 | MIT | direct | Editable install without build isolation |
| `httpcore` | 1.0.9 | BSD-3-Clause | `httpx` |  |
| `httpx` | 0.28.1 | BSD-3-Clause | direct |  |
| `iniconfig` | 2.3.1 | MIT | `pytest` |  |
| `librt` | 0.16.0 | MIT | `mypy` | CPython only |
| `mypy` | 2.4.1 | MIT | direct |  |
| `mypy-extensions` | 1.1.0 | MIT | `mypy` |  |
| `pygments` | 2.21.0 | BSD-2-Clause | `pytest` |  |
| `pytest` | 9.2.0 | MIT | direct |  |
| `pytest-asyncio` | 1.4.1 | Apache-2.0 | direct |  |
| `pytest-cov` | 7.1.0 | MIT | direct |  |
| `ruff` | 0.17.3 | MIT | direct |  |
| `uv` | 0.13.6 | MIT OR Apache-2.0 | direct | Regenerates the locks |

### Kept behind their newest release

Checked on 2026-10-29: every other locked package is its newest stable release on PyPI
(`uv pip compile --upgrade` resolves the same set).

| Component | Locked | Newest | Reason |
| --- | --- | --- | --- |
| Python | 3.14.10 | 3.15.2 | ONNX Runtime 1.31.1, which faster-whisper needs for its voice-activity filter, publishes wheels up to CPython 3.14 only; resolving for 3.15 fails (`onnxruntime>=1.14.0 has no wheels with a matching Python ABI tag (e.g., cp315)`). PyYAML 6.0.3 also has no 3.15 wheels. Move when ONNX Runtime publishes them (risk Q9) |
| redis-py | 6.4.0 | 8.1.0 | Celery talks to Redis through Kombu, and Kombu 5.6.2, the newest stable release, declares `redis>=4.5.2,<6.5` for its Redis transport. Only the pre-release Kombu 5.7.0rc1 (2026-10-18) accepts redis-py 8 (`redis>=6.4.0,<9.0.0`), and pre-releases are not adopted. The backend depends on `celery[redis]`, so the resolver keeps this limit; with `redis==8.1.0` pinned it reaches for the pre-release instead. Move with the next stable Kombu |

### FastAPI and OpenTelemetry

FastAPI 0.142 (2026-09-29) and later depend on `opentelemetry-api` (Apache-2.0) and route every
request through OpenTelemetry instrumentation as soon as a global provider is configured; with
`FASTAPI_OTEL_AUTO_CONFIGURE=true` they also try to add OTLP exporters from `OTEL_*` variables.
Narrastep passes `telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure":
False}` to `FastAPI()` (`backend/app/main.py`), so nothing is instrumented or exported whatever the
environment says, and no OpenTelemetry SDK or exporter is installed. The API package alone does
no network I/O. `tests/test_dependencies.py` checks both (CLAUDE.md rule 11).

## Desktop window (host, downloaded into `desktop/node_modules`)

Installed by `npm ci` in `desktop/` from `desktop/package-lock.json` (P07.1), never committed and
not redistributed.

| Component | Version | License | Note |
| --- | --- | --- | --- |
| Electron | 45.1.0 | MIT | [ADR 0014](decisions/0014-desktop-app-shell.md); its binary download contains Chromium, Node.js and their third-party licenses (`LICENSES.chromium.html`); unsigned |
| `@electron/get`, `@electron-internal/extract-zip` | 5.1.0, 1.0.8 | MIT | Download and unpack the Electron binary |
| `sumchecker` | 3.0.1 | Apache-2.0 | Checks the binary's SHA-256 sum |
| `undici`, `undici-types`, `debug`, `ms`, `env-paths`, `progress` | 7.30.1, 7.24.6, 4.4.3, 2.1.3, 3.0.0, 2.0.3 | MIT | Dependencies of `@electron/get` |
| `graceful-fs`, `semver` | 4.2.11, 7.8.5 | ISC | Dependencies of `@electron/get` |
| `@types/node` | 24.19.2 | MIT | Type declarations required by the `electron` package |

## Bundled files

Files copied into the repository and shipped unmodified are recorded in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) with their version, SHA-256 hash and license text.

| File | License | Package | Note |
| --- | --- | --- | --- |
| Pixelify Sans font (`apps/web/public/fonts/PixelifySans.ttf`) | SIL OFL 1.1 | P04.2 | Variable TTF, version 1.000, served from the same origin; `OFL.txt` beside it |
| `llama.cpp` license text (`backend/third_party/llama.cpp/LICENSE`) | MIT | P06.1 | Copied into the backend image beside the server binaries |

## Planned components

| Component | License | Package | Note |
| --- | --- | --- | --- |
| Gemma 4 E2B and E4B weights | Apache-2.0 | P06 | Downloaded by the user; license shown before download and stored with the files |
| `@angular/localize` | MIT | P04 | English and Italian interface |
| `@electron/packager` | BSD-2-Clause | P07 | Optional local portable build |

## Development tools

| Tool | License |
| --- | --- |
| TypeScript | Apache-2.0 |
| Vitest, ESLint, typescript-eslint, angular-eslint, Prettier, jsdom | MIT |
| Playwright | Apache-2.0 |
| Pytest, Ruff, mypy, uv | MIT (uv: MIT OR Apache-2.0); versions in [Backend Python packages](#backend-python-packages) |

## Local agent tooling

`.claude/` and `skills-lock.json` hold the owner's local agent skills. They are ignored by Git and
never published, so they are not part of this inventory.

## Rules for new dependencies

- Allowed: MIT, BSD, Apache-2.0, ISC, 0BSD, PSF, MPL-2.0, SIL OFL, public domain, and LGPL or GPL
  for separate programs called as subprocesses.
- Not allowed: paid or proprietary licenses, "source-available" licenses (SSPL, RSAL, BSL),
  non-commercial-only model licenses that forbid public redistribution of derived work, and any
  service that requires payment or an account.
- Record each new dependency in this file in the same change.
