# ADR 0015: English and Italian interface

Status: accepted (2026-10-28). Supersedes: the English-only interface rule.

## Context

The owner wants the interface in English and Italian, with an easy switch between them. Code,
comments and documentation stay in English.

## Decision

- Use Angular's built-in internationalization (`@angular/localize`): templates mark text with
  `i18n` attributes and code uses `$localize`.
- Ship one build. Both translation files (`apps/web/src/locale/messages.en.json` and
  `messages.it.json`) are bundled; `loadTranslations()` applies the stored language before
  bootstrap.
- A language switch (`EN` / `IT`) sits in the header and in Settings. Switching stores the choice in
  local storage and reloads the page. The unsaved-review-changes guard runs before the reload.
- The first visit uses the browser language when it is Italian, otherwise English.
- The API keeps English messages and stable error codes. The interface translates by error code and
  falls back to the API message for an unknown code.
- Dates, numbers, durations and sizes use `Intl` with the active locale.
- The export package `README.md` is written in the interface language active at export time. The
  locally generated document uses the transcript language unless the user selects another one.
- A check fails the frontend build when a message ID exists in one language only.

## Alternatives

- Runtime translation library (Transloco, ngx-translate): switches without a reload, but adds a
  dependency and loses Angular's extraction and compile-time checks. The reload costs under a
  second on a local server.
- One build per locale under `/en/` and `/it/`: Angular's default, but doubles the build and needs
  Nginx routing for a two-language local tool.
- Translate the API messages: duplicates text that the interface already maps by code.

## Consequences

- Every new interface text needs both translations in the same change ([CLAUDE.md](../../CLAUDE.md)).
- `npm run extract-i18n` and the message-ID check become part of the frontend checks.
- Tests run in English by default; one browser-contract test runs the main journey in Italian.
