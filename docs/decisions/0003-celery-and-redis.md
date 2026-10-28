# ADR 0003: Celery and Redis background work

Status: accepted (Version 0.2 baseline). Supersedes: none.

## Context

Transcription and frame processing are long-running CPU tasks. They must survive the end of the
HTTP request and report real progress.

## Decision

Use Celery with Redis as broker, temporary result backend, task-revocation transport, short-lived
cancellation hint store and best-effort progress notification channel. Run one prefork worker
process with prefetch one. Keep authoritative job state and cancellation intent in SQLite, and all
durable project state in SQLite and project files.

## Alternatives

- In-process background tasks: do not survive API restarts.
- A custom queue: duplicates mature scheduling and control behavior.
- Several workers: increase memory pressure and SQLite lock contention.

## Consequences

Redis can be rebuilt without losing reviewed projects. Throughput is intentionally serialized.
