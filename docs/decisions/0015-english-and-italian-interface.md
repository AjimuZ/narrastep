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
- A language switch (`EN` / `IT`) sits in the header and in Settings (Settings arrives with P06.4). Switching stores the choice in
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

## Amendment (2026-10-29, P04.4)

Decisions taken while implementing the catalogs:

- **Custom IDs.** Every message has a readable custom ID (`@@shell.menu.projects`), so catalogs
  stay reviewable and editing the English text never orphans a translation. A source that uses
  `i18n` or `$localize` without a custom ID fails the check.
- **No translated constants.** Messages are read when a screen or function runs, never at module
  load, because the catalog is applied by `core/i18n/init.ts` at startup. The `$localize` runtime is
  a build polyfill (`angular.json`).
- **Check.** `apps/web/scripts/check-i18n.mjs` runs before `npm run build` and compares the IDs
  that the sources use with both catalogs, the catalogs with each other, and their placeholders;
  `npm run check:i18n` also compares the English catalog with `ng extract-i18n`.
- **Error text.** English shows the API message as sent because it can carry specifics; every other
  language shows the translation of the stable error code and falls back to the API message for an
  unknown code. The job progress line of the API stays English and is shown only in English.
- **Spoken-language names** come from `Intl.DisplayNames`; the language buttons are named by the
  language in its own language (English, Italiano).
- **Header on narrow screens.** The header holds three items, so below 768 px it can wrap to two
  rows; it then scrolls with the page instead of staying fixed.
- **Dependency.** `@angular/localize` 22.0.6 (MIT) is added to the web app ([Licenses](../LICENSES.md)).
