# Glossary of fixed translations

One word per concept in each language ([UI copy rules](../../../../docs/UI.md#copy-rules)). Use these
terms in every new message and in both catalogs (`messages.en.json`, `messages.it.json`).

| Concept                                 | English          | Italian                  |
| --------------------------------------- | ---------------- | ------------------------ |
| The ZIP made by Export                  | package          | pacchetto                |
| A recording with its metadata           | project          | progetto                 |
| Project type                            | project type     | tipo di progetto         |
| The uploaded recording                  | video            | video                    |
| A transcript part                       | segment          | segmento                 |
| An image taken from the video           | screenshot       | screenshot               |
| Checking and editing the result         | review           | revisione                |
| Transcription and screenshot extraction | processing       | elaborazione             |
| Reading text in images                  | text recognition | riconoscimento del testo |
| A group of files for one chat message   | batch            | lotto                    |
| Transcript                              | transcript       | trascrizione             |
| Unsaved                                 | unsaved          | non salvato              |

Other rules:

- Buttons are verbs: Create project = Crea progetto, Upload video = Carica video, Start processing =
  Avvia elaborazione, Open review = Apri revisione, Save text = Salva testo, Create package = Crea
  pacchetto, Download package = Scarica pacchetto. Italian uses the infinitive/imperative form
  of the verb, never "Clicca".
- Address the user as "tu" (Scegli, Controlla, Riprova), never "Lei" and never in the plural.
- Sentence case, no taglines, the same word for the same thing everywhere.
- Product names and file names are not translated (Narrastep, ChatGPT, `manifest.json`).
- Language names (English, Italiano) are shown in their own language, and spoken-language names
  come from `Intl.DisplayNames` in the active language.
- Italian text is often longer: no layout may truncate or break words at 320 px.
- Keep the placeholders (`{$INTERPOLATION}`, `{$COUNT}`, `{$START_TAG_SPAN}`…) of the English text; the
  check fails when a placeholder differs between the two catalogs.
