# API

The machine-readable contract is [openapi.json](openapi.json). This document describes the human-facing intent of the Version 0.2 API. JSON fields use camelCase, IDs are UUID strings, and timestamps are UTC ISO-8601 strings.

## Common responses

Validation and business failures use this envelope:

```json
{
  "error": {
    "code": "VIDEO_VALIDATION_FAILED",
    "message": "The uploaded file could not be validated as a supported video.",
    "details": {}
  }
}
```

The API uses `400` for malformed domain input, `403` for a feature turned off by configuration, `404` for missing resources, `409` for state conflicts, `413` for oversized uploads, `416` for invalid byte ranges, `422` for schema validation, and `503` for unavailable local infrastructure.

## Endpoints

| Method | Endpoint | Purpose | Success |
| --- | --- | --- | ---: |
| GET | `/api/v1/health` | API, database, Redis, and worker health without secrets | 200 |
| GET | `/api/v1/projects` | List local projects as `{items, total}` | 200 |
| POST | `/api/v1/projects` | Create a draft project | 201 |
| GET | `/api/v1/projects/{projectId}` | Read project details | 200 |
| PATCH | `/api/v1/projects/{projectId}` | Update editable metadata | 200 |
| DELETE | `/api/v1/projects/{projectId}` | Delete a non-busy project and its workspace | 204 |
| GET | `/api/v1/storage` | Disk space used by each project, by kind, and the free space of the data volume | 200 |
| DELETE | `/api/v1/projects/{projectId}/source-video` | Remove the original video once a package of the current review exists | 200 |
| POST | `/api/v1/projects/{projectId}/video` | Stream, probe, and store the only source video | 201 |
| GET | `/api/v1/projects/{projectId}/video` | Stream source video with byte-range support | 200/206 |
| POST | `/api/v1/projects/{projectId}/process` | Queue or retry processing; the optional body carries the project type and spoken language | 202 |
| GET | `/api/v1/jobs/{jobId}` | Read persisted job state | 200 |
| GET | `/api/v1/jobs/{jobId}/events` | Stream progress with SSE | 200 |
| POST | `/api/v1/jobs/{jobId}/cancel` | Request cooperative cancellation | 202 |
| GET | `/api/v1/projects/{projectId}/segments` | List active reviewed segments in order | 200 |
| GET | `/api/v1/segments/{segmentId}` | Read one active segment | 200 |
| PATCH | `/api/v1/segments/{segmentId}` | Update `reviewedText`, `speakerLabel` or both | 200 |
| POST | `/api/v1/segments/{segmentId}/split` | Derive two active segments | 200 |
| POST | `/api/v1/segments/merge` | Merge ordered contiguous active segments | 200 |
| GET | `/api/v1/projects/{projectId}/screenshots` | List retained project candidates | 200 |
| GET | `/api/v1/segments/{segmentId}/screenshots` | List primary and alternatives for a segment | 200 |
| GET | `/api/v1/screenshots/{screenshotId}/thumbnail` | Read or lazily create a local WebP review derivative | 200 |
| GET | `/api/v1/screenshots/{screenshotId}/content` | Read or atomically materialize a local full PNG | 200 |
| POST | `/api/v1/segments/{segmentId}/screenshot-from-video` | Recover the exact source frame and select it as primary | 201 |
| PUT | `/api/v1/segments/{segmentId}/primary-screenshot/{screenshotId}` | Promote a same-project candidate | 200 |
| DELETE | `/api/v1/segments/{segmentId}/primary-screenshot` | Remove the primary association | 204 |
| GET | `/api/v1/projects/{projectId}/export-estimate` | Estimate canonical images, batches, copies, and ZIP bytes | 200 |
| POST | `/api/v1/projects/{projectId}/exports` | Queue an immutable ZIP export | 202 |
| GET | `/api/v1/projects/{projectId}/exports` | List historical exports | 200 |
| GET | `/api/v1/exports/{exportId}` | Poll one export | 200 |
| GET | `/api/v1/exports/{exportId}/download` | Download a completed ZIP | 200 |
| POST | `/api/v1/recordings` | Create a recorder project and open its recording session | 201 |
| GET | `/api/v1/recordings/{recordingId}` | Read the session state and what each track stored | 200 |
| PUT | `/api/v1/recordings/{recordingId}/tracks/{track}/chunks/{sequence}` | Store one `screen` or `microphone` chunk; an identical repeat is a no-op | 201/200 |
| POST | `/api/v1/recordings/{recordingId}/complete` | Join, align, mix and validate the tracks into the source video; never starts processing | 200 |
| DELETE | `/api/v1/recordings/{recordingId}` | Discard an open session, its chunks and its project | 204 |
| GET | `/api/v1/models` | List the pinned generation models with state, progress and license, and whether the kill switch allows downloads | 200 |
| GET | `/api/v1/models/{modelId}` | Read one model's state and download progress | 200 |
| POST | `/api/v1/models/{modelId}/download` | Start, or resume after a failure, the download of a model | 202 |
| POST | `/api/v1/models/{modelId}/cancel` | Cancel a download or its verification and delete the files | 202 |
| GET | `/api/v1/models/{modelId}/import` | Show what the read-only import folder holds for a model, with the license text to show before importing | 200 |
| POST | `/api/v1/models/{modelId}/import` | Copy the model's pinned files from the import folder and verify them; the body only confirms the license | 202 |
| POST | `/api/v1/projects/{projectId}/generations` | Queue the local generation of a document from the reviewed project; the optional body carries the document language (P06.3d) | 202 |
| GET | `/api/v1/projects/{projectId}/generations` | List the project's generations, newest first, as `{items, total}` | 200 |
| GET | `/api/v1/generations/{generationId}` | Poll one generation: status, progress per part, prompt version and model files | 200 |
| POST | `/api/v1/generations/{generationId}/cancel` | Cancel a queued generation at once, or stop a running one within about a second | 202 |
| GET | `/api/v1/generations/{generationId}/document` | Read the Markdown document of a completed generation | 200 |

## Important requests

Project creation accepts `name`, optional `description`, `mode`, and `sourceLanguage`. `mode` is the project type ([ADR 0009](decisions/0009-project-types.md)): one of `task_briefing`, `meeting`, `tutorial`, or `general`, or absent: a project has no type (`mode: null`) until the user chooses one, at the latest when processing starts (P05.4). Projects made by the recorder have none; the new-project form still sends one. `PATCH` can set or change the type but not clear it. The retired Version 0.2 value `technical_walkthrough` is rejected with `VALIDATION_ERROR` on create and update; stored projects were migrated to `task_briefing`, while published export manifests and job configuration snapshots keep the value they were written with. Source language is `auto` or a supported Whisper language code. `glossary` is an optional list of names and terms the speakers use (P09.1); see [Glossary](#glossary-p091).

A segment save (`PATCH /segments/{segmentId}`) accepts `reviewedText`, `speakerLabel` or both;
a body with neither answers `422 VALIDATION_ERROR`:

```json
{
  "reviewedText": "Open the contract page.",
  "speakerLabel": "you"
}
```

`speakerLabel` is `you`, `others` or `null` (clears the label); leaving it out keeps the label,
and any other value answers `422 VALIDATION_ERROR`. A sent label is stored with
`speakerSource: user`. Raw text, words and timing never change, and a `completed` project returns
to `review`. Segment responses carry `speakerLabel` (`you`, `others` or null) and `speakerSource`:
`automatic` when processing set it from the loudness of an in-app recording's microphone and
system audio, `user` when it was set or cleared in Review, and null when no label was ever set
(P05.5, [Recording](RECORDING.md#speaker-labels)). A split copies the label and its source to both
parts; a merge keeps a label only when every part has the same one, with source `user` when the
user set any part, and is unlabelled otherwise.

Split accepts:

```json
{
  "leftReviewedText": "Open the contract page.",
  "rightReviewedText": "Select the active proposal.",
  "splitSeconds": 42.5
}
```

The two text values must concatenate to the current reviewed text and must produce meaningful parts. The timestamp must be strictly inside the segment.

Merge accepts ordered IDs:

```json
{
  "segmentIds": ["first-segment-uuid", "second-segment-uuid"]
}
```

Export creation accepts `includeOriginalVideo`, which defaults to `false`. Export responses carry
`sequence` (the per-project export number) and `downloadName`
([ADR 0013](decisions/0013-descriptive-export-names.md)), for example
`2026-10-28_meeting_sprint-planning_export-02.zip`. Both are fixed when the export is queued. The
download endpoint returns that name in `Content-Disposition`; exports made before names were
stored report and download as `narrastep-{exportId}.zip`.

Export creation also accepts `includeGenerationId` (P06.4c), the id of a completed generation of
the same project. The worker then packs its `document.md` as `05-generated-document/document.md` and
the manifest lists it (`paths.generatedDocument` and `includedGeneration`; both `null` otherwise).
The request is refused with nothing stored, and the project state unchanged, as `404
GENERATION_NOT_FOUND` (no such generation, or one of another project: the same answer for both),
`409 GENERATION_NOT_READY` (not `completed`) or `404 PROJECT_ASSET_MISSING` (its document file is
gone). Export responses report the choice as `includeGenerationId` (`null` when none, also for every
export made before the field existed); a package already built is never rewritten. If the file
disappears after queueing, the export fails with `PROJECT_ASSET_MISSING` instead of leaving the
document out. The document is copied unchanged; its screenshot file names are those the export
selector gives, so they match `02-screenshots/` as long as the review has not changed since the
generation.

Exact-frame recovery accepts a source-relative timestamp:

```json
{
  "timestampSeconds": 42.25
}
```

The timestamp must be non-negative and no later than the same-project source duration. Recovery
creates both local derivatives and makes the new screenshot an explicit user-selected primary.

Export estimation accepts the `includeOriginalVideo` query parameter. It uses the exact same pure
selection and budget function as generation and returns `canonicalImageCount`, `batchCount`,
`batchImageCopyCount`, `estimatedZipBytes`, `isApproximate`, and `redundancyWarning`. A
thumbnail-only PNG is conservatively estimated as `max(thumbnailBytes, width * height * 0.75)`.

Project responses add `activeSegmentCount` and `screenshotCount`. Screenshot responses add
`selectionReason`, `visualChangeScore`, `textChangeScore`, `clusterId`, `isMandatory`,
`materializationState`, and `sharedSegmentCount`. Association responses add `coverageScore` and
`roleRankReason`. Processing-job responses expose the effective strategy, OCR mode, image-budget
profile, and optional numeric metrics. Export responses expose actual versioned metrics after
generation. All fields are additive.

### Recording sessions

Implemented in P05.1; the rules (limits, offsets, interruption, guards) are in
[Recording](RECORDING.md#session-rules-p051). `POST /api/v1/recordings` accepts an optional body
`{"name": "Registrazione 2026-10-29 14:05", "sourceLanguage": "it"}` and creates a `draft`
project of type `general` together with the session. It and `GET /api/v1/recordings/{recordingId}`
return:

```json
{
  "id": "recording-uuid",
  "projectId": "project-uuid",
  "state": "recording",
  "interrupted": false,
  "storedBytes": 3276800,
  "maxBytes": 4294967296,
  "createdAt": "2026-10-29T13:05:00Z",
  "updatedAt": "2026-10-29T13:05:10Z",
  "lastChunkAt": "2026-10-29T13:05:10Z",
  "tracks": [
    {"track": "screen", "chunkCount": 2, "storedBytes": 3145728, "nextSequence": 3,
     "missingSequences": [1], "missingCount": 1, "startOffsetMs": 0},
    {"track": "microphone", "chunkCount": 2, "storedBytes": 131072, "nextSequence": 2,
     "missingSequences": [], "missingCount": 0, "startOffsetMs": 40}
  ],
  "capture": {"chunkSeconds": 5, "videoBitsPerSecond": 2500000, "maxFrameRate": 15}
}
```

`capture` (P05.3) carries the recorder settings `NARRASTEP_RECORDING_CHUNK_SECONDS`,
`NARRASTEP_RECORDING_VIDEO_BITS_PER_SECOND` and `NARRASTEP_RECORDING_MAX_FRAME_RATE`.

`PUT .../tracks/{track}/chunks/{sequence}?startOffsetMs=40` takes the raw chunk bytes with
`Content-Type: video/webm`, `audio/webm` or `application/octet-stream` and answers `201` with
`{"track", "sequence", "sizeBytes", "duplicate": false}`, or `200` with `duplicate: true` when the
same bytes were already stored. `track` is `screen` or `microphone`; `sequence` is 0 to 999999.
`DELETE` answers `204`. Project responses carry `recording`: `{"id", "state", "interrupted"}` for
recorder projects and `null` for uploaded ones. `state` is `recording` or `completed`.

`POST /api/v1/recordings/{recordingId}/complete` (P05.2) saves an open session as its project's
source video. The optional body is `{"name": "Weekly sync", "stopAtGap": false}`: `name` replaces
the project name, and `stopAtGap: true` keeps each track only up to its first missing chunk
(the interface's "Save what was recorded" for an interrupted recording). It answers `200` with the
project representation: `status: "uploaded"`, `videoAsset` set, `recording.state: "completed"`,
`activeJobId: null`. No processing job is queued. The request returns when FFmpeg has finished; the video is copied,
only the audio is encoded. Errors: `409 RECORDING_INCOMPLETE` with
`details` `{"track", "firstMissingSequence", "missingCount"}` when a chunk is missing, or without
details when chunks arrived during the conversion; `409 PROJECT_BUSY`, `409 INVALID_PROJECT_STATE`
for a saved session; `422 VIDEO_VALIDATION_FAILED` for unreadable tracks, `422
VIDEO_AUDIO_TRACK_MISSING` without system audio and microphone; `507 INSUFFICIENT_DISK_SPACE`;
`500 FFMPEG_FAILED`. After a failure the session stays open with all its chunks. After success the
chunks are deleted, so `GET` reports the saved session with empty tracks. The conversion rules are
in [Recording](RECORDING.md#completion-rules-p052).

### Glossary (P09.1)

Every project response carries `glossary`, a list of strings (empty by default, never null).
`POST /projects`, `PATCH /projects/{projectId}` and the body of `POST /projects/{projectId}/process`
accept `glossary` as a list of names and terms. The API normalizes the list before storing it: each
term is trimmed and its inner whitespace collapsed to single spaces, blank terms are dropped, and a
term that repeats an earlier one without regard to case is dropped; order is kept. After
normalization at most 200 terms of at most 80 characters each are accepted, otherwise
`422 VALIDATION_ERROR`; `null` is rejected on `PATCH`. `PATCH` replaces the whole list, and an empty
list clears it.

The glossary reaches the transcriber, so like `sourceLanguage` it can only change while the project
is `draft`, `uploaded` or `failed` (a retry uses the current glossary). Changing it in another state
answers `409 INVALID_PROJECT_STATE`; sending the stored list again is accepted. How the terms are
used is described in [Processing pipeline](PROCESSING_PIPELINE.md#glossary-hints-p091).

### Storage and removing the original video (P09.2)

`GET /api/v1/storage` answers `{totalBytes, freeBytes, items}`. `items` has one entry per project,
largest first (ties by name), with `projectId`, `name`, `status`, `totalBytes`, `categories`
(`source`, `audio`, `transcript`, `screenshots`, `exports`, `other`; they add up to `totalBytes`),
`sourceVideoBytes` (the video file on disk, 0 once removed), `sourceRemovedAt` and
`removalBlockedReason`. Sizes are the bytes of the regular files in the project's workspace;
symbolic links are not followed. `freeBytes` is the free space of the data volume, or null when it
cannot be read. `other` holds diagnostics, scratch files and open recording chunks. The SQLite
file and the data root's `.trash` and `.locks` folders are not counted.

`removalBlockedReason` is null when the original video can be removed now, otherwise one of
`no_video`, `already_removed`, `busy` (queued, processing or exporting) or `package_required`
(the project is not `completed`, or the ZIP of its completed export is not on disk).

`DELETE /api/v1/projects/{projectId}/source-video` deletes every file in the project's `source/`
folder (the video and, for recordings, the kept microphone and system-audio tracks), marks the
video with `sourceRemovedAt` and answers the project's refreshed storage entry (`200`). The project,
transcript, screenshots and packages are untouched. It is refused, with nothing deleted, with
`409 PACKAGE_REQUIRED` before a package of the current review exists, `409 PROJECT_BUSY` during
background work, `409 SOURCE_VIDEO_REMOVED` when it is already removed and `404
PROJECT_ASSET_MISSING` when the project has no video. The removal cannot be undone
([ADR 0018](decisions/0018-remove-original-video-after-export.md)).

After a removal the project's `videoAsset.sourceRemovedAt` is set and: `GET .../video` answers
`410 SOURCE_VIDEO_REMOVED`; `POST /segments/{segmentId}/screenshot-from-video` and
`POST .../exports` with `includeOriginalVideo: true` answer `409 SOURCE_VIDEO_REMOVED`; a package
whose selected screenshots are not all saved as images answers `409 SOURCE_VIDEO_REMOVED` before
anything is queued; `GET /screenshots/{id}/content` for an image that was never saved answers
`404 SOURCE_VIDEO_REMOVED`.

### Starting processing (P05.4)

`POST /api/v1/projects/{projectId}/process` accepts an optional JSON body
`{mode?, sourceLanguage?, glossary?}` (an empty body or `{}` is still valid). `mode` is a project
type, `sourceLanguage` is `auto` or a Whisper language code and `glossary` replaces the project's
glossary (an empty list clears it); all are validated like on creation (`422 VALIDATION_ERROR`). They are stored
in the same statement that moves the project to `queued`, so a refused start (busy project, wrong
state, no worker) changes neither. A project without a type and without `mode` in the body is
refused with `409 PROJECT_TYPE_REQUIRED` before anything is queued; the job's configuration snapshot
records the type that was used. The Process dialog sends `mode` for every start from a Not
processed project; a retry of a failed project sends no body because its type is already stored.

### Local generation models (P06.2b)

Implemented in P06.2b; the states, the pinned files and the license rules are in
[Local generation](LOCAL_GENERATION.md#states). `modelId` is `gemma-4-e2b` (Light) or
`gemma-4-e4b` (Quality). `GET /api/v1/models` answers `{localGeneration, items}`, where
`localGeneration` is `available` or `disabled` (the kill switch `NARRASTEP_LOCAL_GENERATION`) and
`items` has one entry per model; `GET /api/v1/models/{modelId}` answers one entry:

```json
{
  "id": "gemma-4-e2b",
  "label": "Light",
  "state": "downloading",
  "downloadedBytes": 1258291200,
  "totalBytes": 3398849248,
  "files": [
    {"name": "gemma-4-E2B-it-Q4_0.gguf", "sizeBytes": 2841481184},
    {"name": "mmproj-gemma-4-E2B-it-Q8_0.gguf", "sizeBytes": 557368064}
  ],
  "repository": "ggml-org/gemma-4-E2B-it-GGUF",
  "revision": "6938c58e698502037401a5f19f6905f2fb2f7dee",
  "licenseName": "Apache License 2.0",
  "licenseUrl": "https://ai.google.dev/gemma/docs/gemma_4_license",
  "errorMessage": null,
  "updatedAt": "2026-10-30T22:10:00Z"
}
```

`state` is `not_installed`, `downloading`, `verifying`, `installed`, `enabled` or `failed`; a model
that was never touched is `not_installed` with `downloadedBytes: 0` and `updatedAt: null`. The
license name and link are in every entry, so the interface can show them before the download.
`errorMessage` is the readable English reason of a `failed` state and null otherwise; it names a
file and the cause, never a path.

`POST /api/v1/models/{modelId}/download` answers `202` with the entry. The transfer runs in the API
process, requests only the revision-pinned files of the repository (and the storage hosts it
redirects to), stores progress in SQLite and then verifies each file's SHA-256. Poll
`GET /models/{modelId}` for the progress. It works from `not_installed` and from `failed`; after a
failure the partial files are kept and the retry resumes them with an HTTP range request. After a
restart of the API a model left `downloading` or `verifying` continues by itself. `POST
/models/{modelId}/cancel` answers `202` with the entry, usually already `not_installed`, because
the download is stopped, its files are deleted and the state is reset.

Errors, all with the standard envelope: `404 MODEL_NOT_FOUND` for an identifier outside the
catalog; `403 LOCAL_GENERATION_DISABLED` for a download while the kill switch is set (checked before
any network access; cancel stays possible); `409 MODEL_STATE_CONFLICT` for a download of a model
that is already downloading, verifying, installed or enabled, and for a cancel when nothing is in
progress; `507 INSUFFICIENT_DISK_SPACE` when the model would cross the free-space reserve.

`POST /models/{modelId}/enable` answers `200` with the entry in the `enabled` state; it works only
from `installed` and is refused with `403 LOCAL_GENERATION_DISABLED` under the kill switch.
`POST /models/{modelId}/disable` answers `200` with the `installed` entry and keeps the files; it
works only from `enabled`. `DELETE /models/{modelId}` removes the model's files and answers `200`
with the `not_installed` entry; it works only from `installed` or `enabled`. Disable and delete
stay possible under the kill switch. A wrong state gives `409 MODEL_STATE_CONFLICT`, and delete
gives `409 MODEL_IN_USE` while a generation is queued, running or being cancelled (answered from the
`generations` table since P06.3d).

#### Import from disk (P06.2d)

For computers where `huggingface.co` is blocked
([Local generation](LOCAL_GENERATION.md#import-from-disk)). `GET /models/{modelId}/import` reads
the folder the API sees as `/import` (host `data/models-import`) and answers:

```json
{
  "modelId": "gemma-4-e2b",
  "folderAvailable": true,
  "files": [
    {"name": "gemma-4-E2B-it-Q4_0.gguf", "sizeBytes": 2841481184, "found": true},
    {"name": "mmproj-gemma-4-E2B-it-Q8_0.gguf", "sizeBytes": 557368064, "found": false}
  ],
  "ignored": [{"name": "notes.txt", "reason": "not_a_pinned_model_file"}],
  "ignoredCount": 1,
  "licenseName": "Apache License 2.0",
  "licenseUrl": "https://ai.google.dev/gemma/docs/gemma_4_license",
  "licenseText": "…"
}
```

`found` means a regular file of that exact name and size (the SHA-256 is checked by the import).
`ignored` has at most 50 entries and `ignoredCount` counts them all; names are file names, never
paths. A missing or unreadable folder answers `folderAvailable: false`. The interface shows
`licenseText` before it offers the import. The call reads only and works under the kill switch.

`POST /models/{modelId}/import` takes `{"licenseAccepted": true}` and answers `202` with the model
entry in the `downloading` state; poll `GET /models/{modelId}`. It works from `not_installed` and
`failed`. The body has no path and any extra field is `422 VALIDATION_ERROR`; a query parameter
is ignored. Errors: `404 MODEL_NOT_FOUND`, `403 LOCAL_GENERATION_DISABLED`, `422
LICENSE_NOT_ACCEPTED`, `409 MODEL_STATE_CONFLICT`, `404 MODEL_IMPORT_FILES_MISSING` (a pinned name
is absent), `422 MODEL_IMPORT_NOT_PINNED` (the name exists but is not a regular file of the pinned
size) and `507 INSUFFICIENT_DISK_SPACE`. A file with the pinned name and size but another SHA-256
ends in `failed` with an `errorMessage` that says the file is not a pinned model file, and the
copy is deleted. The import folder is never modified.

### Document generation (P06.3d)

Specification: [Local generation](LOCAL_GENERATION.md#generation-job-p063d). The Document screen
(P06.4b) calls them (queue, list, poll, cancel and read the document), tested against a fake API in
the browser; the endpoints themselves are tested with a stand-in model server. The job behind them
also ran with the real model in an opt-in test (P06.3e), which calls the job directly, not these
endpoints; no run has gone through the screen, the API, Celery and a real model together.

`POST /projects/{projectId}/generations` takes an optional `{"language": "it"}` (a Whisper language
code; `auto` or blank means the transcript language) and answers `202` with the queued generation:

```json
{
  "id": "…",
  "projectId": "…",
  "modelId": "gemma-4-e2b",
  "status": "queued",
  "currentStage": "queued",
  "progressPercent": 0,
  "message": "Waiting for the local worker.",
  "partsTotal": 0,
  "partsCompleted": 0,
  "language": null,
  "promptVersion": "generation-prompts-v1",
  "modelFiles": [
    {"name": "gemma-4-E2B-it-Q4_0.gguf", "sizeBytes": 2841481184, "sha256": "8e30…"},
    {"name": "mmproj-gemma-4-E2B-it-Q8_0.gguf", "sizeBytes": 557368064, "sha256": "9406…"}
  ],
  "documentAvailable": false,
  "sizeBytes": null,
  "errorCode": null,
  "errorMessage": null,
  "version": 1,
  "createdAt": "…",
  "startedAt": null,
  "completedAt": null
}
```

- `status` is one of `queued`, `processing`, `cancelling`, `completed`, `failed` and `cancelled`.
  `currentStage` is `queued`, `starting`, `preparing`, `starting_server`, `generating`, `merging`,
  `saving`, `completed`, `failed` or `cancelled`.
- Progress is real work done: `partsTotal` is the number of chronological model requests planned
  (0 until the parts are planned) and `partsCompleted` counts the finished ones. `progressPercent`
  is 5 while the evidence is collected and 10 while the model server starts, then grows with the
  finished parts up to 90, is 97 while the document is saved and 100 when it is stored. A single part needs no merge request; several are merged afterwards. There is
  no event stream for generations: poll `GET /generations/{generationId}` (`version` rises with
  every stored change).
- `promptVersion` and `modelFiles` (name, size and the pinned SHA-256 of each model file) are the
  values that also head the stored document. The stored path is never returned;
  `documentAvailable` says whether `GET …/document` will answer.
- The document is `projects/{id}/generations/{generationId}/document.md`. `GET …/document` answers
  `200` with `text/markdown`, or `409 GENERATION_NOT_READY` until the generation completed.
- Generating does not change the project state. A project may have one generation that is queued,
  running or being cancelled at a time.
- Queueing is refused, with nothing stored: `403 LOCAL_GENERATION_DISABLED` (kill switch),
  `404 PROJECT_NOT_FOUND`, `409 INVALID_PROJECT_STATE` (the project is not in `review` or
  `completed`, or has no transcript), `409 PROJECT_BUSY` (it already has an active generation),
  `409 GENERATION_MODEL_UNAVAILABLE` (the model set by `NARRASTEP_GENERATION_MODEL` is not
  `enabled`, or a file is missing or has the wrong size), `422 VALIDATION_ERROR` (unknown
  language) and `503 REDIS_UNAVAILABLE` or `WORKER_UNAVAILABLE`.
- While a generation is active, `POST …/process` and `DELETE /projects/{id}` answer `409
  PROJECT_BUSY`, the original video cannot be removed, and `DELETE /models/{id}` answers `409
  MODEL_IN_USE`.
- A job that fails stores a stable `errorCode` and an English `errorMessage` free of transcript text:
  `GENERATION_MODEL_UNAVAILABLE`, `LOCAL_GENERATION_DISABLED`, `GENERATION_OUT_OF_MEMORY`,
  `GENERATION_SERVER_FAILED` (the server could not start, stopped or did not answer),
  `GENERATION_FAILED` (anything else, including a project with no usable transcript),
  `GENERATION_INTERRUPTED` (a service restart), `WORKER_BUSY` (other work was running on the
  worker) and `WORKER_UNAVAILABLE`.
- `POST /generations/{generationId}/cancel` cancels a `queued` generation at once. A `processing`
  one becomes `cancelling`; the worker stops the model server within about a second, removes the
  partial files and reports `cancelled`. A finished generation is returned unchanged. Unknown ids
  give `404 GENERATION_NOT_FOUND`.

## Planned changes

Not implemented and not in [openapi.json](openapi.json):

- Copying the generated document as Markdown (P06.4d); the benchmark (P06.5).

## Progress events

The stream emits `snapshot`, `progress`, `completed`, and `failed` events containing the same processing-job representation returned by the polling endpoint. The SSE `id` is the durable job `version`. Comment heartbeats do not alter progress.

## Stable errors

The response envelope carries these stable public codes. Processing and export failures are also
persisted on their job or export record so a browser refresh does not lose the reason.

| Code | Typical HTTP/status | Meaning |
| --- | ---: | --- |
| `PROJECT_NOT_FOUND` | 404 | The requested project does not exist. |
| `PROJECT_BUSY` | 409 | Processing, export, or another guarded project operation is active. |
| `INVALID_PROJECT_STATE` | 409 | The requested lifecycle transition is not allowed (also: upload during an open recording, a change to a completed recording). |
| `SOURCE_VIDEO_ALREADY_EXISTS` | 409 | The project already has its one immutable source upload. |
| `UPLOAD_TOO_LARGE` | 413 | The streamed file, a recording chunk or a whole recording session exceeds its configured limit. |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | The filename extension is not an enabled video type, or a recording chunk is not WebM. |
| `VIDEO_VALIDATION_FAILED` | 422 or failed job | Upload structure or media validation failed. |
| `VIDEO_STREAM_MISSING` | 422 | FFprobe found no usable video stream. |
| `VIDEO_AUDIO_TRACK_MISSING` | 422 or failed job | FFprobe found no audio track required for transcription. |
| `INSUFFICIENT_DISK_SPACE` | 507 | The configured free-space reserve would be crossed. |
| `FFPROBE_FAILED` | 422 or failed job | Bounded media inspection failed or timed out. |
| `FFMPEG_FAILED` | 500 or failed job | Audio normalization, or the conversion of a recording into its source video, failed or timed out. |
| `TRANSCRIPTION_FAILED` | failed job | The offline model is missing or local transcription failed. |
| `FRAME_EXTRACTION_FAILED` | failed job | Screenshot decoding or encoding failed. |
| `REDIS_UNAVAILABLE` | 503 | The local broker is not reachable. |
| `WORKER_UNAVAILABLE` | 503 or failed operation | No local Celery worker is available. |
| `PROCESSING_INTERRUPTED` | interrupted job | Startup reconciliation found abandoned running work; retry is allowed. |
| `PROCESSING_CANCELLED` | cancelled job | Cooperative cancellation completed and derivatives were removed. |
| `PROJECT_TYPE_REQUIRED` | 409 | Processing was requested for a project without a project type and the request did not choose one. |
| `PROJECT_ASSET_MISSING` | 404, 409, or failed operation | Required source, screenshot, or export evidence is unavailable. |
| `PACKAGE_REQUIRED` | 409 | The original video can only be removed once a package of the current review exists on disk (P09.2). |
| `SOURCE_VIDEO_REMOVED` | 404, 409, or 410 | The original video was removed after export, so playback, exact-frame recovery, a package that needs it or a second removal is refused (P09.2). |
| `INVALID_SEGMENT_OPERATION` | 404, 409, or 422 | Split, merge, or edit invariants were not met. |
| `INVALID_SCREENSHOT_ASSOCIATION` | 422 | The selected screenshot is not valid evidence for the segment's project. |
| `INVALID_SCREENSHOT_TIMESTAMP` | 422 | Exact-frame recovery is outside the validated source duration. |
| `SOURCE_DURATION_UNAVAILABLE` | 422 | Exact-frame recovery cannot safely validate the source timestamp. |
| `OCR_UNAVAILABLE` | failed job | The configured local Tesseract executable cannot be used. |
| `OCR_LANGUAGE_UNAVAILABLE` | failed job | Neither the detected local language pack nor English is installed. |
| `OCR_FAILED` | failed job | Selective local OCR failed without exposing its output. |
| `INVALID_BYTE_RANGE` | 416 | A video request contains an unsupported or unsatisfiable byte range. |
| `EXPORT_FAILED` | failed export | The immutable package could not be generated. |
| `EXPORT_NOT_READY` | 409 | A ZIP was requested before export completion. |
| `RECORDING_NOT_FOUND` | 404 | The recording session does not exist or was discarded. |
| `RECORDING_CHUNK_CONFLICT` | 409 | A stored chunk sequence was sent with different bytes, or a track with a different start offset. |
| `RECORDING_INCOMPLETE` | 409 | A recording cannot be saved: a chunk is missing (the first screen chunk, or a gap without `stopAtGap`), or chunks arrived while it was being saved. |
| `MODEL_NOT_FOUND` | 404 | The generation model is not in the pinned catalog (P06.2b). |
| `LOCAL_GENERATION_DISABLED` | 403 | `NARRASTEP_LOCAL_GENERATION=disabled` refuses model downloads (P06.2b). |
| `MODEL_STATE_CONFLICT` | 409 | The model's state does not allow the action: a download of a model that is not `not_installed` or `failed`, or a cancel with nothing in progress (P06.2b); enable, disable or delete from a state the diagram does not allow (P06.2c). |
| `MODEL_IN_USE` | 409 | A generation is queued, running or being cancelled, so the model cannot be deleted (P06.2c; answered from the generation table since P06.3d). |
| `MODEL_DELETE_FAILED` | 500 | The model files could not be removed, so the state is unchanged (P06.2c). |
| `LICENSE_NOT_ACCEPTED` | 422 | An import was requested with `licenseAccepted` false (P06.2d). |
| `MODEL_IMPORT_FILES_MISSING` | 404 | The import folder does not contain a pinned file name, or the folder is missing (P06.2d). |
| `MODEL_IMPORT_NOT_PINNED` | 422 | A file of a pinned name in the import folder is a link, a directory or has another size: not a pinned model file (P06.2d). |
| `GENERATION_OUT_OF_MEMORY` | failed generation | The worker container has less free memory than the model needs (risk K5); stored as the `errorCode` of the generation (P06.3d). |
| `GENERATION_NOT_FOUND` | 404 | The generation does not exist (P06.3d). |
| `GENERATION_NOT_READY` | 409 | The document was requested before the generation completed (P06.3d). |
| `GENERATION_MODEL_UNAVAILABLE` | 409 or failed generation | The configured model is not `enabled`, or one of its files is missing or has the wrong size (P06.3d). |
| `GENERATION_SERVER_FAILED` | failed generation | The local model server could not start, stopped or did not answer (P06.3d). |
| `GENERATION_FAILED` | failed generation | The document could not be generated for another reason, such as a project with no usable transcript (P06.3d). |
| `GENERATION_INTERRUPTED` | failed generation | Startup reconciliation or a redelivered task found the generation running without a worker; start it again (P06.3d). |
| `WORKER_BUSY` | failed generation | The worker found processing, an export or another generation running when the generation started; start it again when that ends (P06.3d). |
| `VALIDATION_ERROR` | 422 | Request fields do not satisfy the public schema. |
