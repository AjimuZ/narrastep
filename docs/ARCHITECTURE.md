# Architecture

## System context

Narrastep is a single-user application that turns a locally uploaded screen recording into reviewable evidence and a portable documentation package. It has no authentication or required external runtime service.

```mermaid
flowchart LR
    User[Local user] -->|127.0.0.1:4200| Web[Angular web application]
    Web -->|Same-origin /api| API[FastAPI]
    API --> DB[(SQLite)]
    API --> Files[(Project workspaces)]
    API --> Redis[(Redis)]
    Redis --> Worker[Celery worker]
    Worker --> DB
    Worker --> Files
    Worker --> Media[FFmpeg, OpenCV, faster-whisper, Tesseract]
```

## Containers and boundaries

- `web` serves static Angular assets through Nginx and proxies `/api` to FastAPI. It contains presentation and client orchestration only. Shared pixel primitives live in `apps/web/src/app/shared/ui/` (styles in `apps/web/src/styles/primitives.scss`); the icon sprite and the bundled font are static files under `apps/web/public/`. The shell (`app.component.ts`: header with the product name and a health pill fed by `GET /api/v1/health`, and the vertical menu) frames every screen; state words come from `shared/status-labels.ts` and project type labels from `shared/project-types.ts`. Interface language ([ADR 0015](decisions/0015-english-and-italian-interface.md)): `apps/web/src/app/core/i18n/` holds the language choice (`language.ts`), the startup loader that applies the active catalog from `apps/web/src/locale/` before any component runs (`init.ts`, imported first by `main.ts`), the language switch with its unsaved-changes checks (`language.service.ts`), locale-aware formatting (`format.ts`) and the translation of stable API error codes (`error-text.ts`). Texts are `i18n="@@id"` template attributes and `$localize` calls with custom IDs, never module-level constants, so they follow the language loaded at startup; the API keeps English messages and codes.
- `api` validates requests and configuration, coordinates transactions, streams local assets, and exposes durable state. Route handlers delegate business behavior to services.
- `worker` performs media and export work. It is the only service that loads the transcription model and runs with concurrency one.
- `redis` provides Celery queues, short-lived results, and best-effort progress notifications. It is not persistent business storage and is not published to the host.

The API joins the local web network and the internal processing network. The worker and Redis join only the internal network, which prevents the processing runtime from reaching the Internet.

## Processing flow

```mermaid
sequenceDiagram
    participant Browser
    participant API
    participant DB as SQLite
    participant Redis
    participant Worker
    participant FS as Local workspace

    Browser->>API: Upload validated source
    API->>FS: Atomic source file
    API->>DB: Media metadata and uploaded state
    Browser->>API: Start processing
    API->>DB: Queued job plus effective configuration snapshot
    API->>Redis: Enqueue task
    Worker->>DB: Processing stage updates
    Worker->>Redis: Progress events
    Redis-->>API: Wake the subscribed job stream
    Worker->>FS: Audio, raw transcript, screenshot derivatives, diagnostics
    Worker->>DB: Segments and associations
    API-->>Browser: SSE snapshots and events
    Worker->>DB: Review state
```

Every worker update is committed to SQLite before a best-effort Redis notification is published.
The notification wakes the subscribed API stream, which reloads the authoritative snapshot from
SQLite. Bounded resynchronization means dropped notifications never lose progress.

## Persistence

SQLite stores structured metadata and relative paths. WAL mode, foreign-key enforcement, a busy timeout, short transactions, and a single worker reduce lock contention. Large media is never stored as a database blob.

Docker mounts a named volume at `/data`. Project workspaces use UUIDs and contain source, audio,
WebP review thumbnails, optional materialized PNGs, immutable transcript evidence, content-free
diagnostics, and export archives. Repository-local `data/` is a placeholder and is not the Docker
storage path.

## Recording sessions

Implemented in P05.1 ([Recording](RECORDING.md#api)). The API, not the worker, owns recording
sessions: `POST /api/v1/recordings` creates the project and the session together, and each chunk
`PUT` streams its body straight into `recording/{track}/` of that project's workspace. Chunk
uploads do not take the per-project operation lock, because the screen and microphone tracks
upload in parallel; they are serialized instead by SQLite's write lock, taken by the first
statement of the chunk transaction (an update of the session row). That transaction checks the
session is still open, the track offset, an existing row for the sequence and the session total
before the file is renamed into place. Discarding takes the project operation lock and uses the
same staged workspace removal as project deletion. Nothing reaches Redis or the worker; no job
is queued.

Completion (P05.2, `services/recording_completion.py`) also runs in the API request, under the
project operation lock, because it is a bounded media conversion like upload validation, not
processing: FFmpeg copies the video and only encodes audio. It reads the chunk list from SQLite,
lets FFmpeg read the chunks in place through the `concatf` protocol, writes the source video and
`source/microphone.webm` (and, with system audio, `source/system.webm`, P05.5) as partial files,
validates the source with the upload's FFprobe rules, and promotes them in the transaction that marks the session `completed`. That transaction's first
statement is the session-row update, the same lock chunk uploads take, so an upload that slipped
in during the conversion is detected and the save is refused instead of losing the chunk. The
chunk rows are deleted in that transaction and the files after it; the startup cleanup removes
chunk files without rows, so a crash in between leaves nothing behind.

The recorder screen (P05.3, `apps/web/src/app/features/recorder/`) is the only client. The
component owns the source checks, the save step and the leave guard; `media-capture.ts` wraps the
browser capture APIs and the microphone level, which is analysed in the page and never sent; and
`recording-engine.ts` runs the two `MediaRecorder` instances and the upload queue. The engine keeps
each chunk in browser memory only until the API acknowledges it, uploads one chunk per track at a
time and retries failures that may heal (no answer, 408, 429, 5xx except 507). The recorder reads
its chunk length, bitrate and frame rate from the session response (`capture`), so the server's
settings are the only source. Saving calls the completion endpoint and nothing else: no job is
queued.

## Review and lineage

`raw-transcript.json` is immutable. Reviewed segments are active views over that evidence. Split and merge archive their source rows, create derived active rows, and record `SegmentLineage` edges in one transaction. This protects original transcription evidence while allowing structural editing.

Screenshot rows represent retained frame candidates. Associations assign primary or alternative
roles to active transcript segments. A screenshot may be associated with more than one segment.
Adaptive rows can remain `thumbnail_only`; full-size access atomically reconstructs and caches the
PNG from the same project source video. Exact-frame recovery creates both derivatives immediately
and records an explicit manual primary selection.

## Screenshot strategy boundary

The original implementation remains behind the `legacy` strategy. The separate `adaptive_v1`
service owns streaming change detection, document-unit fusion, fine decoding, global clustering,
selective OCR, and coverage budgets. Processing-job configuration snapshots prevent API and worker
environment drift. Neither service adds worker concurrency; the Celery worker remains at one.

Tesseract receives local image paths through subprocess argument arrays. TSV output is parsed in
memory into normalized token sets and discarded. Only a numeric text-change score can cross into
persistence. Language packs are image-build dependencies, never runtime downloads.

## Progress and cancellation

The worker persists a monotonic progress version and publishes a wakeup to a job-specific Redis
channel. SSE subscribes to that channel, resynchronizes from SQLite after wakeups and on a bounded
fallback interval, sends an immediate snapshot, uses the version as its event ID, emits heartbeats,
and closes at a terminal state. The browser polls the job endpoint when streaming fails.

Cancellation is best-effort and cooperative. Queued tasks can be revoked immediately; running media
work checks the persisted SQLite flag and a short-lived Redis hint between bounded units. A
reconciler detects jobs left non-terminal after a worker interruption.

## Export generation

Exports are immutable snapshots generated by the Celery worker in a temporary directory. Estimate
and generation share one pure screenshot-selection and budget function. Thumbnail-only canonical
images are materialized inside the temporary export workspace, not the mutable review workspace.
The worker writes JSON, Markdown, SRT, normalized screenshots, prompt material, and optional source
video (the prompt, batch guide and batch task wording come from the project type through
`services/export_guides.py`), creates the ZIP, then atomically promotes it into the project export directory.
The ZIP has one root folder named after the archive and numbered folders in reading order
(`services/export_names.py` builds the names, which `assign_export_name` stores on the export record when it is queued, [Data model](DATA_MODEL.md#export-layout) lists
the layout); the file on disk keeps its UUID name. Historical
exports and their actual metrics do not change when review data changes.

## Diagnostics and privacy

Each completed processing or export stage emits one JSON log event containing identifiers, counts,
durations, and sizes. Terminal per-operation diagnostics are atomically stored below the project
workspace and summarized numerically in SQLite for API, SSE, and UI use. This boundary forbids
transcript text, OCR text, filenames, absolute paths, window titles, and image content.

## Failure handling

- API validation failures use stable error codes and never expose stack traces.
- Processing and export failures are logged with project/job identifiers and persisted without transcript content.
- Partial uploads, processing directories, and export archives are removed after failure or cancellation.
- Missing filesystem assets are reported explicitly rather than replaced with fabricated data.
- Redis or worker unavailability prevents new work from being accepted but does not erase persistent project state.

## Planned boundaries

These boundaries are **planned** and not implemented. They are proposed in ADRs 0010 and 0011.

```mermaid
flowchart LR
    Browser[Browser recorder] -->|chunks| API
    API -->|explicit user download| HF[(huggingface.co, pinned files)]
    API --> Models[(/data/models)]
    Worker[Celery worker] -->|subprocess, loopback| LLM[llama-server CPU]
    LLM --> Models
```

- **Recording ([Recording](RECORDING.md)).** The browser records the screen with system audio and
  the microphone as two tracks and uploads chunks. The API owns recording sessions, joins the
  chunks, aligns and mixes the tracks with FFmpeg, keeps the microphone and the system audio as
  separate tracks for speaker labels and saves the project without queuing processing. No new
  container. The session API and chunk storage exist since P05.1, the completion into the source
  video since P05.2 and the recorder screen since P05.3 (see "Recording sessions" below). Since
  P05.5 the worker labels segments You / Others by comparing the two tracks' loudness
  (`services/speaker_labels.py`, run by `services/processing.py` after transcription), and the
  review API lets the user change the label.
- **Model manager ([Local generation](LOCAL_GENERATION.md)).** The API downloads model files only
  after an explicit user action, through the `local` network, which has an Internet route. It
  verifies SHA-256 hashes and writes to `/data/models`. No project data leaves the computer.
- **Desktop window ([ADR 0014](decisions/0014-desktop-app-shell.md)).** A host Electron shell,
  started by `start.sh` after health, loads only the loopback web app. It supplies the screen and
  window picker, Windows loopback system audio, microphone permission and native downloads. It
  has no other host bridge, and closing it never stops the containers.
- **Generation.** The worker starts `llama-server` as a loopback subprocess for one generation job
  and stops it afterwards. Since P06.1 the backend image holds the pinned CPU server in
  `/opt/llama.cpp`, checked at image build by `scripts/check_llama_server.py`; nothing starts it
  yet ([Local generation](LOCAL_GENERATION.md#server-in-the-backend-image)). The worker keeps no Internet route, and generation is serialized with
  processing by the single worker.

See the decisions under [decisions](decisions/) and the [processing pipeline](PROCESSING_PIPELINE.md).
