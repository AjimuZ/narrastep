# ADR 0006: Server-Sent Event progress

Status: accepted (Version 0.2 baseline). Supersedes: none.

## Context

Processing sends one-way state updates to a browser, must reconnect after a refresh and does not
need bidirectional messaging.

## Decision

Use Server-Sent Events with a durable SQLite snapshot, a monotonic event version, Redis pub/sub
wakeups, bounded database resynchronization, heartbeats and a client polling fallback. Redis only
wakes the API stream; every emitted payload is reloaded from SQLite.

## Alternatives

- WebSockets: add lifecycle and protocol complexity without a bidirectional use case.
- Polling only: adds latency and requests; it remains the fallback.

## Consequences

The transport is browser-native. Nginx buffering must stay disabled. Losing or restarting Redis
cannot interrupt durable progress delivery.
