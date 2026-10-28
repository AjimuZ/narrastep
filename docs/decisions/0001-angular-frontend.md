# ADR 0001: Angular frontend

Status: accepted (Version 0.2 baseline). Supersedes: none.

## Context

Narrastep needs a maintainable local editor with typed HTTP, upload progress, streaming progress,
accessible dialogs and coordinated review state.

## Decision

Use Angular 22 standalone components, strict TypeScript, Signals for local and derived state, RxJS
for asynchronous flows, Router feature loading, HttpClient, SCSS, and Angular CDK where it
materially improves accessibility.

## Alternatives

- React or Vue: both would support the UI but do not match the selected stack.
- NgRx: adds ceremony that a single-user application does not need.

## Consequences

The frontend has no NgModules and no global state framework. Node and Angular versions must stay
compatible, and every upgrade requires the frontend checks in [Development](../DEVELOPMENT.md).
