# Third-party notices

Narrastep itself is published under the [MIT License](LICENSE). This file records the third-party
files that are copied into the repository and shipped unmodified. Packages installed from a
registry are listed in [docs/LICENSES.md](docs/LICENSES.md).

## Pixelify Sans

| Item | Value |
| --- | --- |
| Use | Titles, window titles and large numbers (`--font-pixel`); never paragraphs, labels, tabs or small badges |
| File | `apps/web/public/fonts/PixelifySans.ttf`, served at `/fonts/PixelifySans.ttf` |
| Form | Variable TrueType font (`fvar`, weights 400–700), unmodified, not subset and not converted |
| Version | 1.000 (font `name` table, ID 5; unique ID `1.000;NONE;PixelifySans-Regular`) |
| Upstream | <https://github.com/eifetx/Pixelify-Sans> |
| SHA-256 of the font | `9ba86cd010a4de309d263ceff8e8044092c9db7efda869620cb9ff1c4389e8a5` |
| Copyright | Copyright 2021 The Pixelify Sans Project Authors |
| License | SIL Open Font License, Version 1.1 |
| License text | `apps/web/public/fonts/OFL.txt` (SHA-256 `921ba087e40693958a9b2519d489b8872a894a6f5b1a9bfae37639146c1effe9`), copied next to the font |

The upstream commit of this file was not verified: the copy was taken from a local copy while
offline, so the version number and the file hash above are the verifiable identity. Compare the
hash with the upstream release before publishing. The font is not sold by itself and is not
renamed or modified, as the license requires. The unit test `ui.spec.ts` fails when the font file
no longer matches the hash in this file.
