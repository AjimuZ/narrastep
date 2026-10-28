# ADR 0002: FastAPI backend

Status: accepted (Version 0.2 baseline). Supersedes: none.

## Context

The application needs a typed local API, streamed uploads and downloads, generated OpenAPI and
direct integration with Python media libraries.

## Decision

Use FastAPI with Pydantic request and response schemas, dependency-injected SQLAlchemy sessions,
service modules for business behavior and a versioned `/api/v1` surface.

## Alternatives

- Flask: needs more contract infrastructure.
- Django: brings authentication and administration that Narrastep does not need.

## Consequences

The API contract is generated from code and checked for drift with
`python scripts/export_openapi.py --check`. Route handlers must not contain media processing or
complex persistence logic.
