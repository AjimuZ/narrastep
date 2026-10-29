# User interface

Status: **implemented in English and Italian** for the shell and every existing screen (P04.3 and
P04.4 in the [roadmap](ROADMAP.md)); the browser accessibility pass in both languages is P04.5.
Decisions: [ADR 0012](decisions/0012-pixel-ui-design-system.md),
[ADR 0015](decisions/0015-english-and-italian-interface.md).

## Principles

- 80% modern minimal application, 20% pixel JRPG identity. Readability wins over retro detail.
- One clear primary action per screen.
- Show real state only: progress, counts and sizes come from the API. Unknown values show a
  neutral striped track and the word "Unknown", never an invented number.
- Status always uses a word as well as a color.
- Keep everything local: fonts, icons and styles ship with the web image; no CDN.

## Copy rules

The interface is available in English and Italian
([ADR 0015](decisions/0015-english-and-italian-interface.md)). The rules apply to both languages.

- Write labels as plain nouns and buttons as verbs: "Upload video", "Start processing",
  "Download package".
- No taglines, slogans or promotional adjectives ("powerful", "seamless", "turn work into
  knowledge"). The header shows only the product name.
- One short sentence of help per field at most, and only when the label is not enough.
- Errors say what happened and what to do: "The file has no audio track. Choose a recording with
  sound."
- Use sentence case. Use the same word for the same thing everywhere (for example always
  "package", never also "bundle" or "archive").
- The glossary of fixed translations is `apps/web/src/locale/GLOSSARY.md` (for example
  package = pacchetto, review = revisione, project type = tipo di progetto).
- Italian text is often longer: layouts must not truncate it at 320 px.

### Fixed terms

The English copy uses one word per concept since P04.3. The Italian column is the translation in
`messages.it.json` and in the glossary since P04.4.

| Concept | English | Italian |
| --- | --- | --- |
| The ZIP made by Export | package | pacchetto |
| A recording with its metadata | project | progetto |
| The uploaded recording | video | video |
| A transcript part | segment | segmento |
| An image taken from the video | screenshot | screenshot |
| Checking and editing the result | review | revisione |
| Transcription and screenshot extraction | processing | elaborazione |

### State words

Every status pill takes its word and tone from `apps/web/src/app/shared/status-labels.ts`.

| Kind | Value → word (tone) |
| --- | --- |
| Project | `draft` No video (info), `uploaded` Not processed (info), `queued` Queued (wait), `processing` Processing (wait), `review` Ready for review (ok), `exporting` Creating package (wait), `completed` Package ready (ok), `failed` Failed (error) |
| Processing job | `queued` Queued, `processing` Processing, `cancelling` Cancelling (wait); `completed` Done (ok); `cancelled` Cancelled (info); `failed` Failed, `interrupted` Interrupted (error) |
| Package | `queued` Queued, `processing` Creating (wait); `completed` Ready (ok); `failed` Failed (error) |
| Services (header) | Checking services (info), Services ready (ok), Processing unavailable (wait: Redis or the worker does not answer), Services offline (error: the API does not answer) |

### Project type labels

The new-project form (legend "Project type") and the projects list ("Type" column) use these
labels since P02.1; the Italian column is in `messages.it.json` since P04.4
(`project_type.*` message IDs).

| Value | English label and help | Italian label and help |
| --- | --- | --- |
| `task_briefing` | Task briefing — Someone explains a task or a problem you must solve later. | Spiegazione di un compito — Qualcuno spiega un compito o un problema da risolvere in seguito. |
| `meeting` | Meeting — A discussion whose decisions and key points need a summary. | Riunione — Una discussione di cui riassumere decisioni e punti chiave. |
| `tutorial` | Tutorial — A procedure to repeat or teach, step by step. | Tutorial — Una procedura da ripetere o insegnare, passo per passo. |
| `general` | Generic — Anything else, such as a demo or a recorded note. | Generico — Tutto il resto, per esempio una demo o una nota registrata. |
| (legend, column) | Project type, Type | Tipo di progetto, Tipo |

## Language

The interface is available in English and Italian
([ADR 0015](decisions/0015-english-and-italian-interface.md)). Implemented since P04.4:

- **Switch:** two buttons, `EN` and `IT`, at the right edge of the header (group name "Language" /
  "Lingua"; each button is named by the language in its own language and has `lang`). The current
  language has the pixel cursor and `aria-current="true"`. Below 768 px the header may wrap to two
  rows and then scrolls away with the page instead of staying fixed, so it never covers content. The
  switch also belongs in Settings (P06.4), which does not exist yet.
- **Switching** stores the choice in local storage (`narrastep.language`) and reloads the page. A
  screen with unsaved edits registers a leave check with `LanguageService`; the review screen asks
  "Discard unsaved edits?" before the reload and the reload does not happen when the user keeps the
  edits.
- **First visit:** the stored choice, else Italian when the first browser language is Italian, else
  English. `<html lang>` is set from the active language before the app starts.
- **Catalogs:** `apps/web/src/locale/messages.en.json` and `messages.it.json`; every message has a
  custom ID (`i18n="@@id"`, `i18n-title="@@id"`, `` $localize`:@@id:text` ``). `core/i18n/init.ts`
  loads the active catalog before any component runs, so there is no per-language build.
- **Dates, numbers and sizes** follow the active locale (`DatePipe` with `LOCALE_ID`, `Intl` in
  `core/i18n/format.ts`: "8,0 KB" in Italian). Spoken-language names come from `Intl.DisplayNames`.
- **API errors** are shown by stable code (`core/i18n/error-text.ts`, one message per code of
  [API](API.md#stable-errors)). English shows the API message as sent, because it can carry
  specifics; Italian shows the translation of the code and falls back to the API message for a
  code without translation. The processing job's English progress line is shown in English only,
  because the translated stage name says the same thing.
- **Transcript text** stays in its spoken language, and so do file names.
- **Checks:** `npm run check:i18n` (also run by `npm run build`, without the extraction) fails when an
  ID is in one catalog only, when a source uses an ID that is not in both, when a message has no
  custom ID or an empty text, or when the placeholders differ; with `--extract` it also compares
  `messages.en.json` with `ng extract-i18n`. `npm run extract-i18n` rewrites the English catalog from
  the sources; the Italian text is written by hand.

## Typography

| Use | Font |
| --- | --- |
| Body text, labels, fields, tabs, badges, tables | `--font-ui`: system sans-serif stack |
| Times, IDs, file names, code | `--font-mono`: `ui-monospace, "Cascadia Code", Consolas, monospace` |
| Product name, page titles, window titles at 16 px and above, large numbers (durations, counts) | `--font-pixel`: Pixelify Sans |

Pixelify Sans (SIL OFL 1.1, covers Latin and Latin Extended, so Italian accents render) is bundled
locally at `apps/web/public/fonts/PixelifySans.ttf` and declared with `@font-face` in
`apps/web/src/styles.scss`. It is the unmodified upstream variable font (weights 400–700), used at
weights 500–600: it is not subset or converted to WOFF2, because the license asks for the file to
stay unmodified and no font tooling is part of the build. It is never used for paragraphs, form
labels, tabs or small badges. Its version, file hash and license text are recorded in the root
`THIRD_PARTY_NOTICES.md` ([Licenses](LICENSES.md)), with `OFL.txt` shipped beside the font, and a
unit test fails when the file no longer matches the recorded hash. `font-display: swap` shows the
UI font first, so titles never block on the font.

## Tokens

All tokens live in `apps/web/src/styles/tokens.scss` (P04.1), loaded by `apps/web/src/styles.scss`
with `@use`. The file keeps every palette value of the Version 0.2 interface, has no `--dk-*`
aliases, and is the only place that may hold a color literal. Components use `var(--token)` and
build washes, borders and shadows with `color-mix(in srgb, var(--token) N%, transparent)`. These
roles are defined:

| Role | Token |
| --- | --- |
| Background | `--color-bg` (page fades to `--color-bg-fade`) |
| Deep or inset surface | `--color-bg-deep` |
| Raised surface | `--color-bg-elevated`, and `--color-bg-raised` one step above it |
| Panel | `--color-bg-panel` |
| Hover surface | `--color-surface-hover` |
| Media backdrop, overlay | `--color-media-bg`, `--color-bg-overlay` |
| Text, strong, muted, subtle, disabled | `--color-text`, `--color-text-strong`, `--color-text-muted`, `--color-text-subtle`, `--color-text-disabled` |
| Text on a brand-strong fill | `--color-on-accent` |
| Brand and accent | `--color-brand`, `--color-brand-strong`, `--color-brand-strong-hover`, `--color-accent`, `--color-accent-soft` |
| Focus ring | `--color-focus-ring` |
| Success, warning, danger, info | `--color-success`, `--color-warning`, `--color-danger`, `--color-info` |
| Border and frame | `--color-border`, `--color-border-strong`, `--color-border-accent`, `--color-separator`, `--frame: color-mix(in srgb, var(--color-text-muted) 28%, transparent)` |

Non-color tokens: `--px: 2px`, `--radius: 2px`, `--quick: 120ms`, `--motion-base: 180ms`,
`--font-ui`, `--font-mono`, `--font-pixel` (the bundled Pixelify Sans, then `--font-ui`), the text scale `--text-xs` to `--text-2xl`, the spacing scale `--space-1` to
`--space-6`, `--content-width` and `--page-gutter`.

The Version 0.2 radii (0.5 to 1.25 rem) all became `--radius`, and the `Inter` entry left the UI
font stack. Other styles are unchanged until the restyle in P04.3.

## Shared primitives

Built once under `apps/web/src/app/shared/ui/` and used on every screen:

| Primitive | Use |
| --- | --- |
| Window | Bounded panel with a title bar; stepped corner marks on primary windows only |
| Menu entry | Sidebar and list item with the stepped pixel cursor for current, hover and focus |
| Button | Default, primary and danger; 1 px frame; 1 px press offset |
| Field | Input, select and textarea on the deep surface; visible 2 px focus ring |
| Tabs | Review inspector and settings sections |
| Segmented gauge | Processing stages, upload, model download and recording level |
| Status pill | Project, job, export and model states, always with a word |
| Activity line | Indeterminate work only; never implies a percentage |
| Pixel icon | 16×16 sprite generated with `scripts/pixel_icons.py` from the skill |

No shadows, glow, blur or glass effects. Motion is a fade or a 2–8 px slide of about 140 ms in
steps, and `prefers-reduced-motion` removes it.

### Implementation (P04.2)

All primitive styles are in `apps/web/src/styles/primitives.scss` and use only tokens; the
Angular parts are in `apps/web/src/app/shared/ui/` and are imported from `shared/ui/index.ts`.

| Primitive | Angular element or class | Contract |
| --- | --- | --- |
| Window | `<ns-window title="…" [primary]="true">` | Section named by its title (`h2`); `primary` adds the corner marks |
| Menu entry | `.ns-menu-entry` on an `a` or `button` | The cursor shows on hover, focus and `aria-current` |
| Button | `.ns-button`, `--primary`, `--danger` | 1 px frame; 1 px press offset; no motion under `prefers-reduced-motion` |
| Field | `.ns-field` on `input`, `select`, `textarea` | Deep surface; `aria-invalid="true"` turns the border to the danger color |
| Tabs | `<ns-tabs [tabs] [(selected)] label idPrefix>` | `role="tablist"`; roving tabindex; Left, Right, Home and End; the caller renders the `tabpanel` elements |
| Segmented gauge | `<ns-segmented-gauge [percent] [segments] [tone] label>` | Determinate only; at most 40 cells; rounds down and fills every cell only at 100; `percent` `null` draws the neutral striped track (the label then says "Unknown"); `tone="danger"` for a failed stage |
| Status pill | `<ns-status-pill label tone>` | The label (a word) is required; tones `info`, `ok`, `wait`, `error` |
| Activity line | `<ns-activity-line label>` | `role="progressbar"` without a value; static under `prefers-reduced-motion` |
| Pixel icon | `<ns-pixel-icon name="play">` | 16 px, `aria-hidden`, takes the text color; names are `PixelIconName` |

Since P04.3 every screen uses these primitives and the Version 0.2 classes (`.panel`, `.button`,
`.field`, `.notice`, `.status-badge`, `.skeleton`, `.eyebrow`) are gone. Helper classes in the same
stylesheet:

| Class | Use |
| --- | --- |
| `.ns-button--small` | Compact buttons in list rows |
| `.ns-form-row`, `.ns-label`, `.ns-help`, `.ns-error` | A label, its control, one help line and an error, stacked |
| `.ns-notice`, `--error`, `--ok`, `--wait` | Flat framed message with a colored leading bar |
| `.ns-placeholder` | Static striped block while content loads |
| `.ns-row` | Selectable list row or card; the pixel cursor shows on hover, focus within and `aria-current` |

`styles.scss` adds the page layout (`.page`, `.page-header` with the title in the pixel font and
`.page-header__actions`), `.muted`, `.mono` and the dialog frame (`.ns-dialog`, `__title`, `__body`,
`__actions`). The page background is flat: the Version 0.2 gradients, shadows and blur were removed,
and so were the shadow tokens.

### Icon sprite

Each icon is a 16×16 grid of `#` and `.` in `apps/web/icons/{name}.txt`. The sprite
`apps/web/public/icons/sprite.svg` is generated from them and committed; regenerate it with:

```bash
python /app/skills/ajimu-pixel-ui/scripts/pixel_icons.py apps/web/icons --out apps/web/public/icons/sprite.svg
```

(or `.claude/skills/ajimu-pixel-ui/scripts/pixel_icons.py` where the local skill exists). The
sprite is served from the same origin and referenced as `icons/sprite.svg#i-{name}`. A unit test
checks that every grid is 16×16, that `PIXEL_ICON_NAMES` and the grids match, and that the sprite
holds exactly those symbols. The icons are: check, chevron-right, close, copy, download, edit,
export, folder, info, mic, pause, play, plus, record, refresh, search, settings, stop, trash,
upload, warning. Add an icon by adding its grid and its name, then regenerating.

## Layout

- **Window:** the app opens in the Electron desktop window ([ADR 0014](decisions/0014-desktop-app-shell.md));
  the same pages also work in a browser with `./start.sh --headless`.
- **Shell:** compact header (product name, health pill, language switch) and a vertical menu on
  the left: Projects, Record, New project, Settings. Below 768 px the menu becomes a top bar with the same
  entries.
  - Implemented (P04.3, P04.4, `apps/web/src/app/app.component.ts`): the header shows the product
    name, the health pill and the language switch; the menu holds **Projects** and **New project**.
    Record joins with the recorder (P05.3) and Settings with its screen (P06.4), because planned
    screens must not appear as enabled entries.
  - The current entry has `aria-current="page"`; every project screen counts as Projects except the
    new-project form.
  - The health pill asks `GET /api/v1/health` (same origin, no other request) at start and every
    30 seconds and shows the service words listed under [State words](#state-words).
- **Content:** one main window per screen; secondary information in side windows.
- **Density:** dense but legible; tables and lists use the pixel cursor for the current row.

## Screens

| Screen | Route | Content and primary action |
| --- | --- | --- |
| Projects | `/projects` | Search, type and status filters, sort; one row per project with type, status pill (including **Not processed** and **Recording interrupted**), duration, last change. A Not processed row offers Process. Actions: Record, New project |
| New project | `/projects/new` | Upload path: four type cards (Task briefing, Meeting, Tutorial, Generic) with one-line help; optional name (default `{Type} {YYYY-MM-DD}` with the local date, shown as placeholder and help text); "More options" (collapsed `<details>`, opened automatically when it holds an error) holds language and description; then the video. Action: Create |
| Recorder | `/record` | Opens directly from the menu. Source checks (screen, system audio, microphone level gauge), consent reminder, timer, pause, stop. On stop: optional name, Save or Discard; nothing is processed. Action: Start recording. See [Recording](RECORDING.md) |
| Process | dialog on a Not processed project | Project type cards (required), language under "More options". Action: Start processing |
| Processing | `/projects/:id` | Stage list with segmented gauges and real percentages; cancel and retry. Action: Open review |
| Review | `/projects/:id/review` | Video, transcript segments and screenshot inspector, restyled. Segments from in-app recordings show a You / Others label that can be changed. Every `[mm:ss]` is a button that moves the video to that time. Action: Save |
| Export | `/projects/:id/export` | Package options, size estimate, history titled with each export's stored ZIP name (older exports show `narrastep-{id}.zip`). Action: Create package |
| Document | `/projects/:id/document` | Local generation result per type with the video beside it; every `[mm:ss]` citation is a button that plays the video from that time; regenerate, copy, include in package. Shown only when local generation is installed and enabled |
| Settings | `/settings` | Local generation: install, enable, delete, model size and license; storage use per project |

### Implemented screens (P04.3)

The table above is the target. What the English interface does today:

| Screen | Implemented |
| --- | --- |
| Projects | Title, **New project**; one primary window with search, type, status and sort filters; rows are a table when the list is at least 68 rem wide and labelled cards below. Status pill, type, duration ("Unknown" without a video), segments, screenshots, last change, Open and Delete. Record and Process are not offered yet (P05) |
| New project | Three steps (Project, Video, Processing) each in one primary window; type cards are `.ns-row` radio cards; **Create project**, then **Upload video** with a segmented upload gauge fed by the real upload percentage, then **Start processing** or Process later |
| Processing | Title "Processing" with the project name; the stage list is the primary window. The API reports progress for the whole job only, so one overall gauge shows that value, finished stages show a full gauge, the running stage an activity line and pending stages only a word. **Open review**, Cancel processing, Retry processing; Project, Processing settings and Counters windows beside it |
| Review | Title "Review" with the project name; Segments, Transcript (primary), Video and Screenshot windows with the existing breakpoints, shortcuts, pane width and view switcher; the selected segment row has `aria-current`. Primary action **Save text**; **Export** opens the export screen |
| Export | Title "Export" with the project name; New package (primary: contents, original-video option, **Create package**, the current package with an activity line, because the worker writes fixed stage values rather than a measured percentage), Size estimate, and Packages titled with each stored ZIP name and **Download package** |
| Not found | "Page not found" with **Open projects** |

## Verification

- `python .claude/skills/ajimu-pixel-ui/scripts/check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`
  reports no color literal outside the token file (clean since P04.1).
- Text contrast is at least 4.5:1; large text, focus rings and control borders at least 3:1.
- Every action works with the keyboard in a logical order, with a visible focus ring.
- No horizontal page scroll at 320, 390, 768, 1024, 1280 and 1440 px, and at 200% zoom, in
  English and in Italian (the browser-contract journey runs in both).
- The Playwright browser-contract journey passes in English and in Italian, and the language
  switch test passes.
- `npm run check:i18n` reports no message missing in either language.
- Screens lay out against the content column, not the viewport, where they can: list rows use
  container queries, because the menu takes 13 rem of the width from 768 px up.
