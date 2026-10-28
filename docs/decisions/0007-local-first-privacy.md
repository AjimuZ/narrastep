# ADR 0007: Local-first privacy model

Status: accepted (Version 0.2 baseline). Amended by: [ADR 0011](0011-optional-local-generation.md)
(2026-10-28), which allows one explicit, user-started model download.

## Context

Screen recordings and transcripts can contain confidential operational and personal information.
Narrastep must work without a cloud account or an external AI provider.

## Decision

- Keep all runtime processing, storage, fonts, API assets and model inference local.
- Download dependencies and the transcription model only during image build.
- Publish services only on loopback and keep the worker network without an Internet route.
- Disable telemetry.
- Make project deletion and all-data deletion explicit.

## Alternatives

- Cloud transcription and storage: reduce local resource use but violate the core privacy
  requirement.
- Automatic first-use model downloads: are surprising and make offline behavior unreliable.

## Consequences

The first setup needs a larger download and local disk space. Users control their data lifecycle.
Any later network use needs its own ADR with an explicit, user-initiated design.
