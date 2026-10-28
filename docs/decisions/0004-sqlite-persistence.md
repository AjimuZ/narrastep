# ADR 0004: SQLite local persistence

Status: accepted (Version 0.2 baseline). Supersedes: none.

## Context

Narrastep is a single-user local application that needs relational integrity without an external
database service.

## Decision

Use SQLite through SQLAlchemy and Alembic. Enable WAL, foreign keys and a busy timeout, keep
transactions short and run one background worker.

## Alternatives

- PostgreSQL: adds a service and operational work with no single-user benefit.
- JSON-only metadata: makes transactional split, merge and associations fragile.

## Consequences

Setup stays local and portable. High write concurrency is unsupported, and every schema change
needs an Alembic migration.
