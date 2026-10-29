# Third-party notices

Narrastep itself is published under the [MIT License](LICENSE). This file records the third-party
files that are copied into the repository and shipped unmodified. Packages installed from a
registry are listed in [docs/LICENSES.md](docs/LICENSES.md) with their versions and licenses; the
backend image installs exactly the Python packages of `backend/requirements.lock`
([Backend Python packages](docs/LICENSES.md#backend-python-packages)). In the image most of them
keep their license files in their `*.dist-info` folders under
`/usr/local/lib/python3.14/site-packages/`; ONNX Runtime keeps `LICENSE` and
`ThirdPartyNotices.txt` in its package folder, and CTranslate2, FlatBuffers and `tokenizers` ship no
license file and declare their license (MIT, Apache-2.0, Apache-2.0) only in their metadata
(checked on 2026-10-29, P08.2).

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

## llama.cpp license text

| Item | Value |
| --- | --- |
| Use | License notice for the `llama.cpp` server binaries that the backend image copies from the official image (P06.1) |
| File | `backend/third_party/llama.cpp/LICENSE`, copied to `/opt/llama.cpp/LICENSE` in the backend image |
| Upstream | <https://github.com/ggml-org/llama.cpp/blob/7fe450e19305b828c199d602c23a8337aaa1f03b/LICENSE> (commit of release `v0.5.0`, build `b11146`) |
| SHA-256 | `94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d` |
| Copyright | Copyright (c) 2023-2026 The ggml authors |
| License | MIT License |

The file was downloaded from the upstream commit above on 2026-10-29 and is unmodified. The
binaries themselves are not stored in the repository; they are copied at image build from
`ghcr.io/ggml-org/llama.cpp:server-v0.5.0` pinned by digest (see
[Local generation](docs/LOCAL_GENERATION.md#server-in-the-backend-image)).
