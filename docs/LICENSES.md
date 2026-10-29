# Licenses

Narrastep is published under the [MIT License](../LICENSE) as a free, non-commercial public
project. Every dependency, model, font, tool and service it uses must be available at no cost
under a free or open-source license that allows public redistribution of this repository. Paid
licenses, paid services and paid certificates are not used.

Licenses below are those declared by each upstream project, checked on 2026-10-28. An automated
inventory inside the containers is roadmap item I-15.

## Runtime components (built locally, not redistributed as images)

| Component | Version | License | Note |
| --- | --- | --- | --- |
| Python | 3.12.13 | PSF-2.0 | Base image `python:3.12.13-slim` |
| FastAPI, Pydantic, pydantic-settings | 0.139.0, 2.13.4, 2.12.0 | MIT | |
| Uvicorn | 0.41.0 | BSD-3-Clause | |
| SQLAlchemy, Alembic | 2.0.51, 1.18.5 | MIT | |
| Celery | 5.6.3 | BSD-3-Clause | |
| redis-py | 6.4.0 | MIT | |
| Redis server | 7.2.14 | BSD-3-Clause | Redis 7.4 and later are not under a BSD license; upgrade to Valkey (BSD-3-Clause) instead |
| SQLite | bundled with Python | Public domain | |
| faster-whisper, CTranslate2 | 1.2.1 | MIT | |
| Whisper model weights | `small` | MIT | Downloaded at image build |
| OpenCV (headless) | 4.13.0.92 | Apache-2.0 | |
| NumPy | 2.4.6 | BSD-3-Clause | |
| Pillow | 12.3.0 | MIT-CMU | |
| python-multipart | 0.0.22 | Apache-2.0 | |
| swagger-ui-bundle | 1.1.0 | Apache-2.0 | Serves API docs locally, no CDN |
| FFmpeg (Debian package) | distribution version | GPL-2.0-or-later build | Built locally; if images are ever published, GPL source obligations apply. The image-build transcription check (P08.1) speaks its test sentence with FFmpeg's `flite` source; `libflite1` (BSD-style Flite license) is already installed as an FFmpeg dependency, so nothing is added |
| Tesseract and language data | distribution version | Apache-2.0 | |
| `llama.cpp` server (CPU) | v0.5.0, build b11146 | MIT | Copied into `/opt/llama.cpp` from `ghcr.io/ggml-org/llama.cpp:server-v0.5.0` pinned by digest (P06.1); license text in `backend/third_party/llama.cpp/LICENSE`, copied beside the binaries |
| OpenMP runtime `libgomp1` (Debian package) | distribution version | GPL-3.0-or-later with the GCC Runtime Library Exception | Needed by `llama.cpp`; already installed for FFmpeg and Tesseract |
| Nginx | 1.28.0 | BSD-2-Clause | |
| Node.js (build only) | 22.23.1 | MIT | |
| Angular, Angular CDK | 22.0.x | MIT | |
| `@angular/localize` | 22.0.6 | MIT | Runtime `$localize` and `loadTranslations`; its Babel tooling (`@babel/core`, MIT) is a build-time dependency of the Angular tools and is not shipped in the web image |
| RxJS | 7.8.2 | Apache-2.0 | |
| zone.js, tslib | 0.16.2, 2.8.1 | MIT, 0BSD | |

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
| Pytest, Ruff, mypy | MIT |

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
