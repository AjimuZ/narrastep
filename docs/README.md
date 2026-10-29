# Documentation

| Document | Purpose |
| --- | --- |
| [Requirements](REQUIREMENTS.md) | What Narrastep must do, its constraints and scope |
| [Status](STATUS.md) | What is implemented, experimental or planned today |
| [Roadmap](ROADMAP.md) | Work packages, exit checks, evidence and improvement candidates |
| [Risks](RISKS.md) | Assumptions, risks, open questions and blockers |
| [Architecture](ARCHITECTURE.md) | Containers, boundaries, data flow and failure handling |
| [Data model](DATA_MODEL.md) | Entities, storage layout and migration policy |
| [API](API.md) | Endpoints, requests and stable error codes ([OpenAPI](openapi.json)) |
| [Processing pipeline](PROCESSING_PIPELINE.md) | Transcription, screenshot selection and export algorithms |
| [UI](UI.md) | Design system, copy rules and screens (planned) |
| [Recording](RECORDING.md) | In-browser screen and audio recording (planned) |
| [Local generation](LOCAL_GENERATION.md) | Optional local Gemma 4 document generation (planned) |
| [Licenses](LICENSES.md) | Free-license rule and third-party license inventory |
| [Development](DEVELOPMENT.md) | Setup, checks, tests and conventions |
| [Decisions](decisions/) | One ADR per material decision |

## Decisions

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](decisions/0001-angular-frontend.md) | Angular frontend | Accepted |
| [0002](decisions/0002-fastapi-backend.md) | FastAPI backend | Accepted |
| [0003](decisions/0003-celery-and-redis.md) | Celery and Redis background work | Accepted |
| [0004](decisions/0004-sqlite-persistence.md) | SQLite persistence | Accepted |
| [0005](decisions/0005-filesystem-media-storage.md) | Filesystem media storage | Accepted |
| [0006](decisions/0006-sse-progress.md) | Server-Sent Event progress | Accepted |
| [0007](decisions/0007-local-first-privacy.md) | Local-first privacy model | Accepted |
| [0008](decisions/0008-adaptive-screenshot-selection-and-late-materialization.md) | Adaptive screenshot selection | Accepted |
| [0009](decisions/0009-project-types.md) | Four purpose-based project types | Accepted |
| [0010](decisions/0010-in-browser-recording.md) | In-browser screen recording | Accepted |
| [0011](decisions/0011-optional-local-generation.md) | Optional local multimodal generation | Accepted |
| [0012](decisions/0012-pixel-ui-design-system.md) | Minimal Pixel JRPG design system | Accepted |
| [0013](decisions/0013-descriptive-export-names.md) | Descriptive export names | Accepted |
| [0014](decisions/0014-desktop-app-shell.md) | Electron desktop shell | Accepted |
| [0015](decisions/0015-english-and-italian-interface.md) | English and Italian interface | Accepted |
| [0016](decisions/0016-podman-generated-environment-and-dependency-refresh.md) | Podman launcher, generated environment file and dependency refresh | Accepted (planned, P08) |
| [0017](decisions/0017-valkey-broker.md) | Valkey as the Redis-protocol broker | Accepted (P08.3) |
| [0018](decisions/0018-remove-original-video-after-export.md) | Remove the original video after export | Accepted (P09.2) |
