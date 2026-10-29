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

The API uses `400` for malformed domain input, `404` for missing resources, `409` for state conflicts, `413` for oversized uploads, `416` for invalid byte ranges, `422` for schema validation, and `503` for unavailable local infrastructure.

## Endpoints

| Method | Endpoint | Purpose | Success |
| --- | --- | --- | ---: |
| GET | `/api/v1/health` | API, database, Redis, and worker health without secrets | 200 |
| GET | `/api/v1/projects` | List local projects as `{items, total}` | 200 |
| POST | `/api/v1/projects` | Create a draft project | 201 |
| GET | `/api/v1/projects/{projectId}` | Read project details | 200 |
| PATCH | `/api/v1/projects/{projectId}` | Update editable metadata | 200 |
| DELETE | `/api/v1/projects/{projectId}` | Delete a non-busy project and its workspace | 204 |
| POST | `/api/v1/projects/{projectId}/video` | Stream, probe, and store the only source video | 201 |
| GET | `/api/v1/projects/{projectId}/video` | Stream source video with byte-range support | 200/206 |
| POST | `/api/v1/projects/{projectId}/process` | Queue or retry processing | 202 |
| GET | `/api/v1/jobs/{jobId}` | Read persisted job state | 200 |
| GET | `/api/v1/jobs/{jobId}/events` | Stream progress with SSE | 200 |
| POST | `/api/v1/jobs/{jobId}/cancel` | Request cooperative cancellation | 202 |
| GET | `/api/v1/projects/{projectId}/segments` | List active reviewed segments in order | 200 |
| GET | `/api/v1/segments/{segmentId}` | Read one active segment | 200 |
| PATCH | `/api/v1/segments/{segmentId}` | Update `reviewedText` only | 200 |
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

## Important requests

Project creation accepts `name`, optional `description`, `mode`, and `sourceLanguage`. `mode` is the project type ([ADR 0009](decisions/0009-project-types.md)): one of `task_briefing`, `meeting`, `tutorial`, or `general` (default `tutorial`). The retired Version 0.2 value `technical_walkthrough` is rejected with `VALIDATION_ERROR` on create and update; stored projects were migrated to `task_briefing`, while published export manifests and job configuration snapshots keep the value they were written with. Source language is `auto` or a supported Whisper language code.

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

Export creation accepts `includeOriginalVideo`, which defaults to `false`.

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

## Planned changes

Not implemented and not in [openapi.json](openapi.json):

- Optional `mode` until processing starts ([ADR 0009](decisions/0009-project-types.md), P05.4).
- Descriptive download names ([ADR 0013](decisions/0013-descriptive-export-names.md)).
- Recording session endpoints ([Recording](RECORDING.md#planned-api)).
- Model manager and generation endpoints ([Local generation](LOCAL_GENERATION.md)).

## Progress events

The stream emits `snapshot`, `progress`, `completed`, and `failed` events containing the same processing-job representation returned by the polling endpoint. The SSE `id` is the durable job `version`. Comment heartbeats do not alter progress.

## Stable errors

The response envelope carries these stable public codes. Processing and export failures are also
persisted on their job or export record so a browser refresh does not lose the reason.

| Code | Typical HTTP/status | Meaning |
| --- | ---: | --- |
| `PROJECT_NOT_FOUND` | 404 | The requested project does not exist. |
| `PROJECT_BUSY` | 409 | Processing, export, or another guarded project operation is active. |
| `INVALID_PROJECT_STATE` | 409 | The requested lifecycle transition is not allowed. |
| `SOURCE_VIDEO_ALREADY_EXISTS` | 409 | The project already has its one immutable source upload. |
| `UPLOAD_TOO_LARGE` | 413 | The streamed file exceeds the configured limit. |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | The filename extension is not an enabled video type. |
| `VIDEO_VALIDATION_FAILED` | 422 or failed job | Upload structure or media validation failed. |
| `VIDEO_STREAM_MISSING` | 422 | FFprobe found no usable video stream. |
| `VIDEO_AUDIO_TRACK_MISSING` | 422 or failed job | FFprobe found no audio track required for transcription. |
| `INSUFFICIENT_DISK_SPACE` | 507 | The configured free-space reserve would be crossed. |
| `FFPROBE_FAILED` | 422 or failed job | Bounded media inspection failed or timed out. |
| `FFMPEG_FAILED` | failed job | Audio normalization failed or timed out. |
| `TRANSCRIPTION_FAILED` | failed job | The offline model is missing or local transcription failed. |
| `FRAME_EXTRACTION_FAILED` | failed job | Screenshot decoding or encoding failed. |
| `REDIS_UNAVAILABLE` | 503 | The local broker is not reachable. |
| `WORKER_UNAVAILABLE` | 503 or failed operation | No local Celery worker is available. |
| `PROCESSING_INTERRUPTED` | interrupted job | Startup reconciliation found abandoned running work; retry is allowed. |
| `PROCESSING_CANCELLED` | cancelled job | Cooperative cancellation completed and derivatives were removed. |
| `PROJECT_ASSET_MISSING` | 404, 409, or failed operation | Required source, screenshot, or export evidence is unavailable. |
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
| `VALIDATION_ERROR` | 422 | Request fields do not satisfy the public schema. |
