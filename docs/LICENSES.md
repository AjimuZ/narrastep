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
| FFmpeg (Debian package) | distribution version | GPL-2.0-or-later build | Built locally; if images are ever published, GPL source obligations apply |
| Tesseract and language data | distribution version | Apache-2.0 | |
| Nginx | 1.28.0 | BSD-2-Clause | |
| Node.js (build only) | 22.23.1 | MIT | |
| Angular, Angular CDK | 22.0.x | MIT | |
| RxJS | 7.8.2 | Apache-2.0 | |
| zone.js, tslib | 0.16.2, 2.8.1 | MIT, 0BSD | |

## Planned components

| Component | License | Package | Note |
| --- | --- | --- | --- |
| `llama.cpp` server (CPU) | MIT | P06 | Copied from the official image pinned by digest |
| Gemma 4 E2B and E4B weights | Apache-2.0 | P06 | Downloaded by the user; license shown before download and stored with the files |
| Pixelify Sans font | SIL OFL 1.1 | P04 | Bundled locally; source revision, file hash and `OFL.txt` recorded in `THIRD_PARTY_NOTICES.md` |
| `@angular/localize` | MIT | P04 | English and Italian interface |
| Electron | MIT | P07 | Desktop window ([ADR 0014](decisions/0014-desktop-app-shell.md)); downloaded into `desktop/node_modules`; unsigned |
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
