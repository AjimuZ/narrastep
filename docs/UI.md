# User interface

Status: **planned** (P04 in the [roadmap](ROADMAP.md)). The Version 0.2 interface is still in
use. Decision: [ADR 0012](decisions/0012-pixel-ui-design-system.md).

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
- Keep a short glossary of fixed translations in `apps/web/src/locale/GLOSSARY.md` (for example
  package = pacchetto, review = revisione, project type = tipo di progetto).
- Italian text is often longer: layouts must not truncate it at 320 px.

### Project type labels

The new-project form (legend "Project type") and the projects list ("Type" column) use these
labels since P02.1. The interface is English-only until the P04.4 catalogs exist; the Italian
column is the agreed translation that P04.4 moves into `messages.it.json`.

| Value | English label and help | Italian label and help |
| --- | --- | --- |
| `task_briefing` | Task briefing — Someone explains a task or a problem you must solve later. | Spiegazione di un compito — Qualcuno spiega un compito o un problema da risolvere in seguito. |
| `meeting` | Meeting — A discussion whose decisions and key points need a summary. | Riunione — Una discussione di cui riassumere decisioni e punti chiave. |
| `tutorial` | Tutorial — A procedure to repeat or teach, step by step. | Tutorial — Una procedura da ripetere o insegnare, passo per passo. |
| `general` | Generic — Anything else, such as a demo or a recorded note. | Generico — Tutto il resto, per esempio una demo o una nota registrata. |
| (legend, column) | Project type, Type | Tipo di progetto, Tipo |

## Language switch

- An `EN` / `IT` switch in the header and in Settings; the current language is marked with the
  pixel cursor and has `aria-current`.
- Switching saves the choice and reloads; unsaved review edits trigger the existing confirmation
  first.
- `<html lang>` follows the active language.

## Typography

| Use | Font |
| --- | --- |
| Body text, labels, fields, tabs, badges, tables | `--font-ui`: system sans-serif stack |
| Times, IDs, file names, code | `--font-mono`: `ui-monospace, "Cascadia Code", Consolas, monospace` |
| Product name, page titles, window titles at 16 px and above, large numbers (durations, counts) | `--font-pixel`: Pixelify Sans |

Pixelify Sans (SIL OFL 1.1, covers Latin and Latin Extended, so Italian accents render) is bundled
locally as a WOFF2 subset of the upstream variable font, used at weights 500–600. It is never used
for paragraphs, form labels, tabs or small badges. Its source revision, file hash and `OFL.txt` go
in `THIRD_PARTY_NOTICES.md` ([Licenses](LICENSES.md)).

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
| Shadow (removed with the restyle in P04.3) | `--color-shadow`, `--color-shadow-panel`, `--color-shadow-strong` |

Non-color tokens: `--px: 2px`, `--radius: 2px`, `--quick: 120ms`, `--motion-base: 180ms`,
`--font-ui`, `--font-mono`, `--font-pixel` (falls back to `--font-ui` until Pixelify Sans is
bundled in P04.2), the text scale `--text-xs` to `--text-2xl`, the spacing scale `--space-1` to
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

## Layout

- **Window:** the app opens in the Electron desktop window ([ADR 0014](decisions/0014-desktop-app-shell.md));
  the same pages also work in a browser with `./start.sh --headless`.
- **Shell:** compact header (product name, health pill, language switch) and a vertical menu on
  the left: Projects, Record, New project, Settings. Below 768 px the menu becomes a top bar with the same
  entries.
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

## Verification

- `python .claude/skills/ajimu-pixel-ui/scripts/check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`
  reports no color literal outside the token file (clean since P04.1).
- Text contrast is at least 4.5:1; large text, focus rings and control borders at least 3:1.
- Every action works with the keyboard in a logical order, with a visible focus ring.
- No horizontal page scroll at 320, 390, 768, 1024, 1280 and 1440 px, and at 200% zoom.
- The existing Playwright browser-contract journey passes after the restyle.
