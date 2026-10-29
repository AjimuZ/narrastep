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
| ZIP export | Implemented | Numbered archive layout with a root folder named `{date}_{type}_{slug}_export-{nn}`, manifest schema 0.3.0 (P03.2); the file on disk stays `narrastep-{UUID}.zip`. The number and ZIP name are stored when the export is queued (P03.3, migration `20261029_0004`) and the download and history use them; exports without a stored name download as `narrastep-{UUID}.zip` |
| Project types | Implemented | `task_briefing`, `meeting`, `tutorial`, `general` (P02.1); migration `20261029_0003` maps `technical_walkthrough` to `task_briefing`. Type-specific screenshot profile, export guide, generation prompt and batch evidence prompts (P02.2); new-project form with type cards, optional default name and "More options" (P02.3) |
| Export name builders | Implemented | Slug, archive and screenshot names (P03.1); used by the archive layout (P03.2) and the stored download name (P03.3) |
| Design token file | Implemented | `apps/web/src/styles/tokens.scss` holds every color and the non-color scales; the `--dk-*` aliases are gone and the color check reports no literal outside it (P04.1). The pixel look itself is still planned (P04.3) |
| Shared pixel primitives, icon sprite and Pixelify Sans | Implemented | Window, tabs, gauge, status pill, activity line and icon components in `apps/web/src/app/shared/ui/`, button, field and menu-entry classes in `styles/primitives.scss`, 21 icons in a 16×16 sprite, and the unmodified Pixelify Sans TTF with its notice in `THIRD_PARTY_NOTICES.md` (P04.2). No screen uses them yet (P04.3); rendering was checked by unit tests and the production build only, not in a browser |
| Docker local stack | Implemented | Web, API, single worker, Redis, named volumes |
| Automated unit, API and UI tests | Implemented | Last recorded floor (2026-10-29): 148 backend passed, 1 opt-in integration skipped; 49 frontend passed |
| Browser contract journey | Implemented | Desktop and mobile Chromium, six responsive widths |
| Full-stack browser journey | Implemented | Opt-in; real mock-enabled Compose stack; inspects the ZIP |
| Docker Compose smoke test | Verified (2026-08-05) | Images build; health, CSS/CSP rendering and full-stack export passed |
| Selective OCR comparison | Experimental | Tokens stay in memory; only numeric scores persist |
| Adaptive benchmark gate | Pending verification | `legacy` stays default |
| Portable `project.json` snapshot | Not implemented | SQLite is authoritative; no import consumer exists |

## Planned

| Capability | Status | Package |
| --- | --- | --- |
| Shell, screen restyle and plain copy | Planned | P04.3 to P04.5 |
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
