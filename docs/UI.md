# User interface

Status: **implemented in English and Italian** for the shell and every existing screen (P04.3 and
P04.4 in the [roadmap](ROADMAP.md)); the browser accessibility and responsive pass in both
languages ran in P04.5 (see [Verification](#verification)).
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
| Names and terms saved to help the transcription (P09.1) | glossary | glossario |

### State words

Every status pill takes its word and tone from `apps/web/src/app/shared/status-labels.ts`.

| Kind | Value → word (tone) |
| --- | --- |
| Project | `draft` No video (info), `uploaded` Not processed (info), `queued` Queued (wait), `processing` Processing (wait), `review` Ready for review (ok), `exporting` Creating package (wait), `completed` Package ready (ok), `failed` Failed (error) |
| Processing job | `queued` Queued, `processing` Processing, `cancelling` Cancelling (wait); `completed` Done (ok); `cancelled` Cancelled (info); `failed` Failed, `interrupted` Interrupted (error) |
| Package | `queued` Queued, `processing` Creating (wait); `completed` Ready (ok); `failed` Failed (error) |
| Recording (open session of a recorder project) | still receiving chunks: Recording (wait); no chunk for `NARRASTEP_RECORDING_INTERRUPTED_AFTER_SECONDS`: Recording interrupted (error). Shown instead of the project word "No video" (P05.3) |
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
| (project without a type) | Not chosen | Non scelto |

A project has no type until the user chooses one (saved recordings, P05.4). The Type column shows
**Not chosen**, and the Type filter offers it as an entry that lists those projects.

## Language

The interface is available in English and Italian
([ADR 0015](decisions/0015-english-and-italian-interface.md)). Implemented since P04.4:

- **Switch:** two buttons, `EN` and `IT`, at the right edge of the header (group name "Language" /
  "Lingua"; each button is named by the language in its own language and has `lang`). The current
  language has the pixel cursor and `aria-current="true"`. Below 768 px the header may wrap to two
  rows and then scrolls away with the page instead of staying fixed, so it never covers content. The
  switch is also in [Settings](#settings-p064a) (P06.4a), with the same behavior and the languages
  named in full ("English", "Italiano").
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
| Frame of a field | `--color-field-border` (3:1 on every surface that holds a field; P04.5) |

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
| Button | Default, primary and danger; 1 px frame; 1 px press offset. The danger text sits on the deep surface (elevated on hover) because `--color-danger` reaches 4.5:1 only there |
| Field | Input, select and textarea on the deep surface with a `--color-field-border` frame and 4.5:1 placeholder text; visible 2 px focus ring |
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
  - Implemented (P04.3, P04.4, P05.3, P09.2, P06.4a, `apps/web/src/app/app.component.ts`): the header
    shows the product name, the health pill and the language switch; the menu holds **Projects**,
    **Record**, **New project**, **Storage** (Italian Spazio; it reuses the `copy` icon because
    a new icon would be an asset task) and **Settings** (Italian Impostazioni; the `settings` icon).
    A planned screen never appears as an enabled entry: the Document screen has none, because it
    is reached from a project (the **Document** link in Review, [shown only while generation is
    available](#document-p064b)).
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
| New project | `/projects/new` | Upload path: four type cards (Task briefing, Meeting, Tutorial, Generic) with one-line help; optional name (default `{Type} {YYYY-MM-DD}` with the local date, shown as placeholder and help text); "More options" (collapsed `<details>`, opened automatically when it holds an error) holds language, description and glossary; then the video. Action: Create |
| Recorder | `/record` | Opens directly from the menu. Source checks (screen, system audio, microphone level gauge), consent reminder, timer, pause, stop. On stop: optional name, Save or Discard; nothing is processed. Action: Start recording. See [Recording](RECORDING.md) |
| Storage | `/storage` | A Total window (space used by projects and free space of the computer) above one row per project, largest first: name, status pill, total, the non-empty parts (Original video, Audio, Transcript, Screenshots, Packages, Other) and one action. A project whose current review is in a package offers **Remove original video**; every other project shows why not ("Create a package of the current review first." with a **Create package** link, "Wait until the project finishes its current work.", "The original video was removed." with the date, "This project has no original video yet."). Removal opens a confirmation dialog (danger button, Escape and Cancel keep everything) that names the project, the space freed (“This frees 500.0 MB…”) and what stops working; afterwards the row and total update and a polite status line says how much was freed. Errors appear in an alert with **Try again** (P09.2) |
| Process | dialog on a Not processed project | Project type cards (required), language and glossary under "More options". Action: Start processing |
| Processing | `/projects/:id` | Stage list with segmented gauges and real percentages; cancel and retry. Action: Open review |
| Review | `/projects/:id/review` | Video, transcript segments and screenshot inspector, restyled. Segments from in-app recordings show a You / Others label that can be changed. Every `[mm:ss]` is a button that moves the video to that time. Action: Save |
| Export | `/projects/:id/export` | Package options, size estimate, history titled with each export's stored ZIP name (older exports show `narrastep-{id}.zip`). Action: Create package |
| Document | `/projects/:id/document` | Local generation result per type with the video beside it; every `[mm:ss]` citation is a button that plays the video from that time; regenerate, copy, include in package. Shown only when local generation is installed and enabled. Implemented in P06.4b except the clickable citations (P06.6), copy and the package (P06.4c, P06.4d) ([Document](#document-p064b)) |
| Settings | `/settings` | Local generation: install, import from the `data/models-import` folder (the license text is shown first, from `GET /models/{id}/import`), enable, delete, model size and license; storage use per project. The language switch is here too. Implemented in P06.4a except the import and the storage use ([Settings](#settings-p064a)) |

### Implemented screens (P04.3)

The table above is the target. What the English interface does today:

| Screen | Implemented |
| --- | --- |
| Projects | Title, **Record** and **New project** (also in the empty state); one primary window with search, type, status and sort filters; rows are a table when the list is at least 68 rem wide and labelled cards below. Status pill (Recording or Recording interrupted for an open recorder session, P05.3), type, duration ("Unknown" without a video), segments, screenshots, last change, Open and Delete. Open on an open recorder session leads to the recorder. A Not processed row shows **Process** instead of Open (Open only led to the old start step) and opens the Process dialog; the Type column reads "Not chosen" for a project without a type, and the Type filter has a "Not chosen" entry (P05.4) |
| Process | A modal dialog (`ns-process-dialog`, `shared/dialogs/`) titled "Process “{name}”" on a Not processed project, opened from Projects, from the Processing screen's **Start processing** and from step 3 of a resumed new-project wizard when the project has no type. It explains what processing does, shows the four type cards unselected (the stored type is preselected), and keeps the spoken language and the glossary under "More options". **Start processing** without a type keeps the dialog open and shows "Choose a project type to start processing." in an alert notice; an invalid language code or glossary opens "More options" with its error. Cancel or Escape starts nothing and returns focus to the trigger. The choices are sent with the start request (`mode`, and `sourceLanguage` and `glossary` only when changed; clearing the field sends an empty list). It is 40 rem wide at most and scrolls inside itself below the viewport height (P05.4) |
| Recorder | Title "Record"; Sources window (screen, system audio, microphone with device list and level gauge, consent and headset notices, in the desktop window on Windows a notice that notification sounds are recorded too, in the desktop window elsewhere a note that system audio is not shared, **Start recording**); Recording window (state pill, length, uploaded size, sources, gauge, retry notice, Pause or Resume, **Stop**); Save recording window (length, uploaded size, parts still uploading, optional name, **Save** or **Save what was recorded**, Discard); Recording saved window (Open projects, Record again). `/record?recording={id}` opens an interrupted session. Details in [Recording](RECORDING.md#recorder-screen-p053) (P05.3) |
| New project | Three steps (Project, Video, Processing) each in one primary window; type cards are `.ns-row` radio cards; the **Glossary** field (a text area, one name or term per line, optional) sits under "More options" and is sent as a normalized list, with an error for more than 200 terms or a term over 80 characters, and help that says a long list is cut after about 500 characters (P09.1); **Create project**, then **Upload video** with a segmented upload gauge fed by the real upload percentage, then **Start processing** or Process later |
| Processing | Title "Processing" with the project name; the stage list is the primary window. The API reports progress for the whole job only, so one overall gauge shows that value, finished stages show a full gauge, the running stage an activity line and pending stages only a word. **Open review**, Cancel processing, Retry processing; Project, Processing settings and Counters windows beside it |
| Review (video removed) | After the original video is removed, the Video window shows "The original video was removed from this computer after the package was created. The transcript and the saved screenshots are still here." instead of the player, and **Recover current frame** is disabled; the Export screen disables **Include the original video** with the same explanation (P09.2) |
| Review | Title "Review" with the project name; Segments, Transcript (primary), Video and Screenshot windows with the existing breakpoints, shortcuts, pane width and view switcher; the selected segment row has `aria-current`. Primary action **Save text**; **Export** opens the export screen; **Document** (Italian Documento), before it, opens the Document screen and exists only while local generation is available (P06.4b). The Transcript window has a **Speaker** select (No label, You, Others; Italian Chi parla: Nessuna etichetta, Tu, Altri) beside the confidence, saved immediately without touching unsaved text; an automatic label adds "Set from the audio levels", a labelled segment shows its speaker in the Segments row, and the result is announced in the polite live region ("Speaker of segment 1: Others."). The select stays enabled while saving so focus never jumps; a choice made meanwhile is sent after the first, and a refused save puts the stored label back and shows the error (P05.5) |
| Export | Title "Export" with the project name; New package (primary: contents, original-video option, **Create package**, the current package with an activity line, because the worker writes fixed stage values rather than a measured percentage), Size estimate, and Packages titled with each stored ZIP name and **Download package** |
| Not found | "Page not found" with **Open projects** |

### Settings (P06.4a)

`/settings` (`apps/web/src/app/features/settings/`) has two windows and uses only existing tokens
and primitives (window, menu entry for the language buttons, button, status pill, segmented gauge,
activity line, notice, pixel icons, confirmation dialog):

- **Language:** one button per language ("English", "Italiano") with `lang`, the pixel cursor and
  `aria-current="true"` on the current one. A click behaves like the header switch: it stores the
  choice, runs the leave checks and reloads on the same route.
- **Local generation** (primary window): a list with one row per model from `GET /models`: name
  ("Light" / Leggero, "Quality" / Qualità; the identifier is not shown), one help line, state pill
  (Not installed, Downloading, Verifying, Installed, Enabled, Failed), download **Size** and
  **License** as a link to `licenseUrl` (new tab, `rel="noopener noreferrer"`).
  - **Install** (not installed) opens a panel inside the row, with nothing requested yet: the
    license name and link and the address printed beside it, the download size and the source
    (`huggingface.co/{repository}`). **Accept the license and download** calls `POST
    /models/{id}/download`; **Cancel** closes the panel and puts focus on the model's name. Focus
    also moves to the model's name after an action removes the button that had it; the name is
    not a Tab stop.
  - **Downloading** shows a segmented gauge from `downloadedBytes` / `totalBytes` (rounded down,
    named "Download of the Light model, 40%") and "1.2 GB of 3.4 GB"; **Verifying** shows an
    activity line, because verification reports no percentage. **Cancel download** calls `POST
    /models/{id}/cancel`. The screen polls `GET /models/{id}` every 2 seconds only while a model
    is in one of these two states, also for a download that was already running when the screen
    opened, and stops when the screen is left.
  - **Failed** shows the API's `errorMessage` in English and a general sentence in other
    languages (the reason has no translation), with **Try again**, which resumes the download.
  - **Installed** offers **Enable** and **Delete**; **Enabled** offers **Disable** and **Delete**.
    Delete opens the confirmation dialog (danger button, Escape and Cancel keep the model) that
    names the model and the space freed. A polite status line says what happened; a refusal (for
    example `MODEL_IN_USE`) appears in an alert with **Try again**.
  - With `localGeneration: "disabled"` (`NARRASTEP_LOCAL_GENERATION=disabled`) the window shows
    one notice that names the setting and no model, button or link. The API would still allow
    disable and delete then, but the screen offers none of them.
- **Not in the screen yet:** the import from `data/models-import` (`GET` and `POST
  /models/{id}/import`) and the storage use per project listed in the screen table above. The
  license link opens a new tab in a browser; the desktop window cancels every popup, so there it
  does nothing, and only the license panel of **Install** prints the address for the user to read.

### Document (P06.4b)

`/projects/:id/document` (`apps/web/src/app/features/document/`, Italian **Documento**) uses only
existing tokens and primitives (window, button, status pill, segmented gauge, activity line,
notice, pixel icons):

- **Availability.** The screen exists only while local generation is available: `GET /models`
  answers `localGeneration: "available"` **and** at least one model has the state `enabled`
  (`isGenerationAvailable`). `generationAvailableGuard` checks this when the route is entered; if
  it is false, or the API cannot be reached, the address redirects to `/projects/:id/review`. The
  **Document** link in the Review header (`GenerationAvailability.check()`, same rule) is rendered
  only when the answer is true, so a disabled, not installed, only installed or failed model leaves
  no link and no route. A model disabled while the screen is open is not noticed until the next
  navigation; the API then refuses a new generation with `GENERATION_MODEL_UNAVAILABLE`, which the
  screen shows in an alert.
- **Generation window** (primary): one sentence on what happens (local, nothing is sent), a
  **Status** heading with a pill (Not generated, Waiting to start, Generating, Cancelling,
  Completed, Failed, Cancelled) and one button. **Generate document** (or **Regenerate** when a
  finished document exists) calls `POST /projects/{id}/generations` and is disabled while the
  request is in flight. While a generation is queued, running or being cancelled the button is
  **Cancel generation** (`POST /generations/{id}/cancel`; **Cancelling…** and disabled once the
  API reports `cancelling`).
- **Progress** is real, from polling `GET /generations/{id}` every 2 seconds (the poll also resumes
  a generation that was running when the screen opened, and stops when the screen is left): the
  stage in words (Waiting for the local worker, Collecting the reviewed evidence, Starting the
  local model, Writing the document, Merging the parts, Saving the document), and once the parts
  are planned a segmented gauge from `progressPercent` named "Document generation, 33%" with
  "Parts written: 1 of 3" (`partsCompleted` of `partsTotal`) in a polite live region. Before the
  parts are planned an activity line stands in, because no percentage exists yet.
- **Failure and cancellation.** A failed generation shows its `errorCode` and a sentence: the
  API's English `errorMessage` in English, the translation of the code in other languages
  (`GENERATION_OUT_OF_MEMORY`, `GENERATION_SERVER_FAILED`, `GENERATION_FAILED`,
  `GENERATION_INTERRUPTED`, `GENERATION_MODEL_UNAVAILABLE`, `WORKER_BUSY`), and the button offers
  to generate again. A cancelled one says nothing was kept.
- **Result.** The newest completed generation appears in two windows: **Generated document** (the
  text read from `GET /generations/{id}/document`) and **Video** (the project's native player,
  beside the text from 48 rem of content width and below it on narrow windows). While a new
  generation runs, the previous document stays visible. If the original video was removed after
  export, the player is replaced by the same notice as in Review. The stored Markdown is shown as
  text only: the leading comment with the prompt version and model hashes is dropped, `#`/`##`
  headings and lines that are only `**bold**` (how the Light model writes its titles) become
  headings, `-`, `1.` lists and paragraphs are kept, and `**bold**` inside text is bold. Nothing
  is rendered as HTML.
- **Not offered yet, and therefore not shown at all** (not as a disabled control): citations that
  play the video from their time (P06.6; `[00:20-00:31]` stays plain text), copying the result as
  Markdown (P06.4d), and including it in a package (P06.4c). No language choice is offered; the
  document follows the transcript language.
- **Focus.** After **Generate document** or **Cancel generation** removes the button that had focus,
  focus moves to the **Status** heading (not a Tab stop). A polite status line says what happened.

### Desktop window (P07.1)

The Electron window ([ADR 0014](decisions/0014-desktop-app-shell.md)) shows the same screens. Its
own interface is small and follows the page language (`<html lang>`), or the system language
before the app has loaded:

- **Window:** titled by the page; 1280 × 800, reduced to fit a smaller screen, never below
  320 × 480; background `--color-bg` while loading; no menu bar on Windows and Linux.
- **Zoom:** Ctrl (Cmd on macOS) with `+` or `=`, `-` and `0` step through 25% to 500% and reset,
  as in a browser, because there is no View menu.
- **Leave dialog:** when a screen asks before unloading (recording, uploading, unsaved edits) and
  the user closes the window, a native dialog titled "Leave this page?" ("Uscire da questa
  pagina?") says "A recording, an upload or unsaved edits may be lost." ("Una registrazione, un
  caricamento o modifiche non salvate potrebbero andare persi.") with **Stay** (default, Escape)
  and **Leave** (**Resta**, **Esci**).
- **Not reachable page:** when the app does not answer, a local page without scripts says
  "Narrastep is not reachable" ("Narrastep non è raggiungibile"), names the address, tells the user
  to start Narrastep with `./start.sh` and offers a **Try again** (**Riprova**) link; colors come
  from `--color-bg`, `--color-text` and `--color-focus-ring`.

## Verification

The browser checks live in `apps/web/tests/e2e/accessibility.spec.ts` (with `support/fake-api.ts` and
`support/contrast.ts`) and run with `npx playwright test`, in desktop and mobile Chromium. They
open all nine screens (Projects, New project, Recorder, Processing, Processing failed, Review,
Export, Storage, Not found) in English and Italian against a deterministic fake API, and run the
confirm, Storage removal, split and screenshot-preview dialogs on top of them. The Recorder is checked in its first state
(the source checks); `tests/e2e/recorder.spec.ts` adds contrast and 320 px reflow of the checks,
recording and save windows with fake devices in both languages. `tests/e2e/process.spec.ts` covers the Process dialog on a saved recording (journey, refusal
without a type, keyboard, language option, glossary, contrast, 320 px, 200% and 400% zoom) in both languages.
`tests/e2e/storage.spec.ts` covers the Storage journeys (figures, reasons, confirmed removal, Cancel
and Escape, API refusal, failed load, empty list) and `tests/e2e/glossary.spec.ts` the glossary field
of New project (terms sent, limits, keyboard, contrast, 320 px, 200% and 400% zoom), in both languages (P09.3).
The fake API's first segment carries an automatic You label, so every Review check covers the
speaker select, and `tests/e2e/speaker-labels.spec.ts` changes and clears it with the keyboard,
reports a refused save and checks contrast, 320 px and 200% zoom in both languages (P05.5). The
last full run (2026-10-30, P09.3) passed 592 tests: 214 accessibility checks, 30 Process dialog
tests, 18 glossary tests, 12 Storage tests, 10 speaker-label tests, 5 recorder tests and 4
permissions-policy tests per Chromium project, plus the 6 contract tests. Since P06.4b the Document
screen (before the first generation, with a result, and Review with the **Document** link) is in
every contrast, keyboard, reduced-motion and reflow check, plus its progress, cancelled and failed
states, in both languages; `tests/e2e/document.spec.ts` covers the journeys against the fake API
(generation with progress per part, result beside the video, regenerate, cancel, failure, and the
screen and link absent when no model is enabled or generation is turned off).

- `python .claude/skills/ajimu-pixel-ui/scripts/check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`
  reports no color literal outside the token file (clean since P04.1).
- **Contrast.** Every visible text run, placeholder, hovered button, row and menu entry, and each
  dialog is at least 4.5:1 against the colors painted behind it, or 3:1 for text of 24 px (18.66 px
  bold). Field frames (inputs, selects, textareas and the drawn project-type radios) and the focus
  ring are at least 3:1 on every surface token. Button frames are not measured: the button text and
  fill identify them (WCAG 1.4.11 asks for a boundary only where it is the sole identification).
  Native sliders, checkboxes and the file input are drawn by the browser. Text over an image is not
  measured by color; no screen has any.
- **Keyboard.** Tab visits every visible control once, in document order (the menu first, then the
  page; read against the layouts by eye), with
  an accessible name, never covered when focused and never trapping; a screenshot of each focused
  control differs from the unfocused one, so the focus is visible (project-type cards, the Review
  view switcher and the file picker show it on their label or card). Radio groups share one `name`
  and move with the arrow keys. Dialogs are modal (`aria-modal`), keep Tab and Shift+Tab inside,
  return focus to the control that opened them and close with Escape (confirm and split dialogs
  take Escape as Cancel even though a backdrop click never closes them).
- **Reduced motion.** With `prefers-reduced-motion: reduce` no element or pseudo-element keeps an
  animation or transition longer than a frame, hover included; without the preference the
  processing screen does animate, so the check can fail.
- **Reflow, zoom and size.** No horizontal page scroll, no control cut off at a side and no text
  cut off by `overflow: hidden` at 320, 390, 768, 1024 and 1440 px, at 200% zoom of a 1280 px
  window (640 px at device scale 2) and at 400% (320 px at scale 4), in English and Italian. At 390
  px and below every control is at least 24 × 24 px (WCAG 2.5.8). Every media and container query
  is written in `rem`, so a larger browser font size moves the breakpoints like zoom does. Headless
  Chromium here ignores the default-font-size preference, so a 200% *font size* setting is covered
  through that rule and the 640 px case, not by a run with the preference itself.
- No horizontal page scroll at 320, 390, 768, 1024, 1280 and 1440 px in the browser-contract
  journey, in English and in Italian.
- The Playwright browser-contract journey passes in English and in Italian, and the language
  switch test passes.
- `npm run check:i18n` reports no message missing in either language.
- Screens lay out against the content column, not the viewport, where they can: list rows use
  container queries, because the menu takes 13 rem of the width from 768 px up.
