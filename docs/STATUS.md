# Implementation status

This matrix describes what exists in the code today (Version 0.2). A capability is
**Implemented** only when code and automated tests exist; **Verified** adds a recorded manual or
environment check. **Planned** names its [roadmap](ROADMAP.md) package.

## Version 0.2

| Capability | Status | Notes |
| --- | --- | --- |
| Project CRUD and persistence | Implemented | SQLite metadata and UUID workspaces |
| Confirmed project deletion | Implemented | Busy projects are protected |
| Video upload and validation | Implemented | MP4, WebM, MOV, MKV; FFprobe required |
| Byte-range video playback | Implemented | Single byte ranges for HTML5 seeking |
| Local transcription | Implemented | faster-whisper, CPU/int8, model baked into the image |
| Legacy screenshot extraction | Implemented | Default; temporal anchors, quality score, consecutive pHash deduplication |
| Adaptive screenshot selection | Experimental | `adaptive_v1`; not the default until the benchmark gate passes |
| Late PNG materialization | Implemented | WebP review thumbnails; PNG on demand or at export |
| Content-free diagnostics | Implemented | Numeric SQLite summaries and project-local JSON |
| Durable processing progress | Implemented | SQLite, SSE and polling fallback |
| Cooperative cancellation and retry | Implemented | Checked between bounded units |
| Transcript review | Implemented | Raw evidence preserved |
| Segment split and merge | Implemented | Archived sources and lineage |
| Screenshot reassignment and recovery | Implemented | Primary and alternatives; exact-frame recovery |
| ZIP export | Implemented | Transcript, distinct primary screenshots, ChatGPT upload batches; file named `narrastep-{UUID}.zip` |
| Project types | Implemented | `task_briefing`, `meeting`, `tutorial`, `general` (P02.1); migration `20261029_0003` maps `technical_walkthrough` to `task_briefing`. Type-specific screenshot profile, export guide, generation prompt and batch evidence prompts (P02.2); new-project form with type cards, optional default name and "More options" (P02.3) |
| Export name builders | Implemented | Slug, archive and screenshot names (P03.1); pure functions, not yet used by the export (P03.2, P03.3) |
| Docker local stack | Implemented | Web, API, single worker, Redis, named volumes |
| Automated unit, API and UI tests | Implemented | Last recorded floor (2026-10-29): 100 backend passed, 1 opt-in integration skipped; 38 frontend passed |
| Browser contract journey | Implemented | Desktop and mobile Chromium, six responsive widths |
| Full-stack browser journey | Implemented | Opt-in; real mock-enabled Compose stack; inspects the ZIP |
| Docker Compose smoke test | Verified (2026-08-05) | Images build; health, CSS/CSP rendering and full-stack export passed |
| Selective OCR comparison | Experimental | Tokens stay in memory; only numeric scores persist |
| Adaptive benchmark gate | Pending verification | `legacy` stays default |
| Portable `project.json` snapshot | Not implemented | SQLite is authoritative; no import consumer exists |

## Planned

| Capability | Status | Package |
| --- | --- | --- |
| Descriptive export names and layout | Planned | P03 |
| Pixel interface, pixel title font and plain copy | Planned | P04 |
| English and Italian interface with a switch | Planned | P04 |
| In-browser screen, system-audio and microphone recording, saved without processing | Planned | P05 |
| You / Others speaker labels for in-app recordings | Planned | P05.5 |
| Optional local Gemma 4 generation | Planned | P06 |
| Clickable `[mm:ss]` references in Review and Document | Planned | P06.6 |
| Electron desktop window opened by `start.sh` | Planned | P07 |
| Other improvements (launcher for PowerShell, glossary, masking, search and more) | Candidate | [Roadmap](ROADMAP.md#improvement-candidates) |

## Last documentation check

2026-10-28: `python scripts/check_docs.py` passed after the documentation rewrite. The test
suites were not re-run because no application code changed.
