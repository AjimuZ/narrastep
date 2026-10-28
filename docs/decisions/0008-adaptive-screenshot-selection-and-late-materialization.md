# ADR 0008: Adaptive screenshot selection and late materialization

Status: accepted (2026-07-20). Supersedes: none.

## Context

The legacy screenshot pipeline samples regular and transcript-relative timestamps, writes every
retained representative as PNG and removes only consecutive duplicates. It is predictable, but long
recordings keep repeated screens, create unnecessary export copies and make review expensive in
disk usage and visual noise.

Narrastep must improve recall and redundancy without cloud processing, runtime model downloads,
extra worker concurrency, persisted OCR text or a GPU requirement.

## Decision

- Add the versioned `adaptive_v1` strategy behind `NARRASTEP_SCREENSHOT_SELECTION_STRATEGY`.
  `legacy` stays the default until the local benchmark gates pass.
- Each processing job snapshots its effective strategy, OCR mode, project mode, algorithm version,
  thresholds, Tesseract executable, alternative count and image-budget profile. The worker uses
  the snapshot, not mutable process configuration.
- `adaptive_v1` performs a streaming 320-pixel grayscale scan, adaptive change-window detection,
  transcript-unit fusion, bounded fine decoding, explainable quality scoring, global medoid
  clustering, selective Tesseract comparison and mode-specific coverage budgets.
- OCR runs through argument arrays and keeps normalized tokens in memory only. Persisted metadata
  holds numeric change scores, never OCR text.
- Review stores 640-pixel WebP derivatives at quality 82. A full PNG is created atomically only for
  full-size access, explicit recovery or selection, and export generation.
- Export estimation and generation call the same pure selection function.
- Content-free diagnostics are written atomically per operation; SQLite keeps a numeric summary.

## Alternatives

- Persisting OCR text: rejected; tokens are a transient detail and can contain sensitive content.
- PaddleOCR, CLIP/SigLIP, TransNetV2 or vision-language interpretation: rejected for this version
  because of image size, runtime complexity or hardware requirements.
- Eagerly storing every full PNG: rejected; it defeats the disk-saving goal.
- Remote analysis and telemetry: rejected by [ADR 0007](0007-local-first-privacy.md).

## Consequences

- Existing PNG-backed projects are backfilled as `png_ready` and never rewritten or deleted.
- Manual evidence, mandatory transient or error states and final stable states can exceed the
  automatic targets and hard caps.
- Review and export support screenshots whose full PNG does not exist yet.
- The backend image installs Tesseract with English, Italian, German, French and Spanish packs.
- `adaptive_v1` stays experimental until the recall, missed-state, redundancy, PNG, batch-copy,
  CPU-time and RAM gates pass on the local benchmark corpus.
- `legacy` stays a configurable fallback for at least one release after any default change.
