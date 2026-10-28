# Contributing to Narrastep

## Changes

Keep each change focused; do not mix feature work with unrelated cleanup. Never commit uploaded
videos, generated user data, model weights, real `.env` files or secrets.

## Code style

- Keep every project artifact in English except source-language transcripts.
- Prefer explicit business naming, guard clauses, small responsibilities and defensive handling of
  incomplete external data.
- Comment contracts, not syntax: each module states its responsibility; each function states what
  it returns, requires and how it fails.
- Use Signals for Angular local state and RxJS for asynchronous flows.
- Keep processing logic out of FastAPI route handlers and persistence logic out of UI components.
- Interface work follows [UI](docs/UI.md) and the `ajimu-pixel-ui` skill: existing color tokens
  only, plain text, no promotional copy.

## Tests and documentation

Run the checks in [Development](docs/DEVELOPMENT.md) for every subsystem you changed. Add or update
tests for behavior, failure modes and regressions. Update the documents the change affects, the
roadmap item, `docs/STATUS.md` and `CHANGELOG.md` in the same change.

## Commit messages

- Concise English imperative subject, ideally 72 characters or fewer, describing the resulting
  behavior: `Name export packages after the project and date`.
- A body only when it explains the reason, the checks or the limits.
- No mention of AI or automated authorship and no `Co-Authored-By` trailers.

## Licenses

Narrastep is public and non-commercial. Add only dependencies, models, fonts and tools with a free
license that allows public redistribution, and record them in [Licenses](docs/LICENSES.md). Paid
licenses, paid services and paid certificates are not accepted.

## Review checklist

- [ ] The change is scoped and business-readable.
- [ ] User input, paths, subprocess arguments and media are validated safely.
- [ ] Formatting, lint, type checking, tests and builds pass; results are noted honestly.
- [ ] Documentation and OpenAPI output match the code.
- [ ] Roadmap, `docs/STATUS.md` and `CHANGELOG.md` are current.
- [ ] No telemetry, cloud dependency, remote runtime asset, host install or privacy regression.
- [ ] Generated data, model weights and secrets are absent from the diff.

## Security and privacy

Report security or privacy issues privately to the repository owner. Do not include real videos,
transcripts, filenames or filesystem paths in public issues. A contribution must not make local
services reachable from other machines by default.
