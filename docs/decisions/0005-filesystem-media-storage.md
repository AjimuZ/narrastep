# ADR 0005: Local filesystem media storage

Status: accepted (Version 0.2 baseline). Supersedes: none.

## Context

Videos, WAV files, frame candidates and export archives are large and need direct access from
FFmpeg and the browser streaming API.

## Decision

Store media below a configured local data root in UUID project workspaces. Store only relative
paths and metadata in SQLite. Docker uses a named volume, not a synchronized repository folder.

## Alternatives

- Database blobs: increase locking and backup cost.
- Directories named after projects: allow collisions and unsafe paths. Human-readable names apply
  only to downloaded artifacts (see [ADR 0013](0013-descriptive-export-names.md)).
- Cloud object storage: violates the local-first scope.

## Consequences

A database backup alone does not contain media, and project deletion must coordinate database and
filesystem cleanup. Path containment and atomic promotion are mandatory.
