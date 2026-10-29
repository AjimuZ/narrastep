# ADR 0018: Remove the original video after export

Status: accepted (2026-10-29, P09.2). Refines: [ADR 0005](0005-filesystem-media-storage.md) and
[ADR 0007](0007-local-first-privacy.md).

## Context

A recording is the largest file of a project, often hundreds of megabytes, and it stays on disk
for as long as the project exists. Once the package is made, the owner often only needs the
package. The only way to reclaim the space was to delete the whole project, which also deletes
the transcript, the screenshots and the review work. The video is also what the worker reads to
rebuild a screenshot as a full image and to recover an exact frame, so removing it has
consequences that must be stated and guarded.

## Decision

- A **Storage** screen (`/storage`, `GET /api/v1/storage`) shows the bytes each project uses,
  split by workspace folder, and the free space of the data volume. It measures regular files
  only and never follows symbolic links.
- `DELETE /api/v1/projects/{projectId}/source-video` removes the original recording: every file
  in the project's `source/` folder, which is the video and, for recorder projects, the kept
  microphone and system-audio tracks. The project, the transcript, the screenshots and every
  package stay.
- The API removes the video only when the project is idle **and a package of the current review
  exists on disk**: the project is `completed` (editing returns a project to `review`, so the
  newest package then no longer matches) and a completed export's ZIP file is present. Otherwise
  it answers `409 PACKAGE_REQUIRED`, `409 PROJECT_BUSY`, `409 SOURCE_VIDEO_REMOVED` (already
  gone) or `404 PROJECT_ASSET_MISSING` (no video), and deletes nothing.
- The interface asks for confirmation in a dialog that names the project and the space freed and
  lists what stops working. The API itself does not need a token: it is a local single-user
  service, like project deletion, and the guard that matters (a package exists) is checked on
  the server.
- The record is kept: `media_assets.source_removed_at` (migration `20261029_0009`) marks the
  removal and the file name, size and duration stay for display and history. Files are deleted
  first and the record is marked afterwards, so an interrupted removal can be repeated and a
  record never claims a removal that did not happen.

## Alternatives

- **Delete the video when the export finishes.** Hidden data loss; an owner who exports often
  while reviewing would lose the video before the review is final.
- **Let the owner delete the video without a package.** Loses the only copy of the evidence
  without any output. Rejected.
- **Rebuild all full-size screenshots before deleting.** Keeps new packages possible but spends
  a large part of the space the removal is meant to free. Rejected; a package that needs a frame
  only the video held is refused up front instead.

## Consequences

- After removal the video cannot be played (`410 SOURCE_VIDEO_REMOVED` on the video route; Review
  shows a notice), exact frames cannot be recovered (`409`), a package cannot include the
  original video (`409`) and a new package is refused (`409 SOURCE_VIDEO_REMOVED`) when a
  selected screenshot exists only as a position in the video. A screenshot already saved as an
  image (`screenshots/full/`) still exports, so transcript-only changes remain exportable for
  projects whose images were saved. Packages already created are untouched.
- Removal is permanent; the file is deleted, not moved to a trash folder.
- The storage figures are an accounting aid: they do not include the SQLite file or the
  `.trash` and `.locks` folders of the data root.
