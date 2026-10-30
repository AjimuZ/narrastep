# ADR 0017: Valkey as the Redis-protocol broker

Status: accepted (2026-10-29, P08.3). Refines: [ADR 0003](0003-celery-and-redis.md). Answers open
question Q10 in [Risks](../RISKS.md).

## Context

[ADR 0003](0003-celery-and-redis.md) uses a Redis server as Celery broker, temporary result
backend and progress channel. The stack ran `redis:7.2.14-alpine`. Redis 7.4 and later are licensed
under RSALv2 or SSPLv1, and Redis 8.0 added AGPLv3 as a third option (risk K9). The dependency
refresh of [ADR 0016](0016-podman-generated-environment-and-dependency-refresh.md) takes the newest
release whose license [Licenses](../LICENSES.md#rules-for-new-dependencies) allows. Those rules
forbid RSAL and SSPL and allow the GPL family only for programs called as subprocesses, and the
broker is a network service. The question was whether to keep Redis 7.2 (BSD-3-Clause) or move to
Valkey, the Linux Foundation fork of Redis 7.2.4 under BSD-3-Clause.

## Decision

The `redis` service runs the Valkey project's image `valkey/valkey:9.1.3-alpine`, pinned by the
digest of its index, with `valkey-server --appendonly yes` and a `valkey-cli ping` health check.

- The service name, the `redis://redis:6379` URLs, the `narrastep-redis` volume, the `redis` key of
  `GET /api/v1/health`, the `REDIS_UNAVAILABLE` error code and the redis-py client stay as they
  are: they name the protocol, and Valkey speaks it (`INFO` reports `redis_version:7.2.4` to
  clients).
- The volume is kept. Upstream documents that Valkey reads the RDB and AOF files of Redis 7.2 (this
  was not tried on the P08.3 worker). Its entrypoint changes ownership of `/data` to `valkey`,
  uid 999, the same uid the `redis:7.2.14-alpine` image used (checked in both images). Redis holds
  only temporary coordination state ([ADR 0003](0003-celery-and-redis.md)), so if a file cannot be
  read, removing the volume loses no project data.

## Alternatives

- **Keep Redis 7.2 (7.2.17 is the newest patch, BSD-3-Clause).** License-compatible, but an old
  maintenance branch: every later refresh would face the same question again.
- **Redis 8.x under AGPLv3.** Newest Redis, but the license rules would need an exception for a
  network copyleft service.
- **Another broker (RabbitMQ, SQLite-based queues).** Changes Celery's transport, the result
  backend and the progress channel at once, far beyond a dependency refresh.

## Consequences

- `docker-compose.yml` and `docs/LICENSES.md` name Valkey 9.1.3 with its digest, and
  `backend/tests/test_container_images.py` keeps the broker on Valkey.
- At the time of this decision, the image build and a healthy stack on a real container engine had
  not been run. They ran on 2026-10-30 with Docker: `valkey-server --version` reported 9.1.3, health
  reported `ok` for the database, broker and worker, and a generated video went through Celery on
  Valkey from upload to export (`backend/scripts/full_job_check.py`; output in the
  [roadmap](../ROADMAP.md), P08.3). That stack started on an empty volume, so reading the files of
  Redis 7.2 from an existing `narrastep-redis` volume remains untested.
- Kombu's Redis transport and redis-py treat Valkey as Redis 7.2. A Kombu or redis-py release that
  requires Redis 8-only commands would need a new decision.
