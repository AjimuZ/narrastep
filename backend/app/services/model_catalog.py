"""Pinned catalog of the local generation model files: revision, names, sizes and SHA-256."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

HUGGING_FACE_BASE_URL: Final = "https://huggingface.co"
MODEL_LICENSE_NAME: Final = "Apache License 2.0"
MODEL_LICENSE_URL: Final = "https://ai.google.dev/gemma/docs/gemma_4_license"


@dataclass(frozen=True)
class ModelFile:
    """One file of a model option as published in the pinned repository revision.

    `name` is a plain file name with no directory part; `size_bytes` and `sha256` come from the
    repository's file listing (the LFS object id) and are the only values a download or an import
    is accepted against.
    """

    name: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ModelOption:
    """A selectable generation model: the weights file and the vision projector it needs.

    `revision` is the commit of `repository` that the sizes and hashes were read from, so a later
    change upstream cannot alter what Narrastep accepts.
    """

    model_id: str
    label: str
    repository: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def total_bytes(self) -> int:
        """Return the download size of all files of the option."""
        return sum(item.size_bytes for item in self.files)

    def file_url(self, file: ModelFile) -> str:
        """Return the revision-pinned download address of `file`; nothing is requested here."""
        return f"{HUGGING_FACE_BASE_URL}/{self.repository}/resolve/{self.revision}/{file.name}"


# Read on 2026-10-30 from https://huggingface.co/api/models/ggml-org/gemma-4-<size>-it-GGUF (the
# repository `sha`) and its `tree/main` listing (each file's size and LFS `oid`, a SHA-256).
GEMMA_4_E2B: Final = ModelOption(
    model_id="gemma-4-e2b",
    label="Light",
    repository="ggml-org/gemma-4-E2B-it-GGUF",
    revision="6938c58e698502037401a5f19f6905f2fb2f7dee",
    files=(
        ModelFile(
            name="gemma-4-E2B-it-Q4_0.gguf",
            size_bytes=2_841_481_184,
            sha256="8e30dff3ac4c8434c49a7036fa15564bdbb6044e42bf04550bf1a096ad7e6a52",
        ),
        ModelFile(
            name="mmproj-gemma-4-E2B-it-Q8_0.gguf",
            size_bytes=557_368_064,
            sha256="9406f99c16d68cda4f1f0552192dcc99021ea1fc6d2fd50b1dc3ccf30d04b292",
        ),
    ),
)

GEMMA_4_E4B: Final = ModelOption(
    model_id="gemma-4-e4b",
    label="Quality",
    repository="ggml-org/gemma-4-E4B-it-GGUF",
    revision="a69f018d7678a2b9f5e705f8903d6fb29b9b25dc",
    files=(
        ModelFile(
            name="gemma-4-E4B-it-Q4_0.gguf",
            size_bytes=4_590_807_392,
            sha256="a555b900214b477d8880e7832e0b8925e139b0159640036b09fe472b6f2097f2",
        ),
        ModelFile(
            name="mmproj-gemma-4-E4B-it-Q8_0.gguf",
            size_bytes=559_874_816,
            sha256="197f49a93027f9843772bd24a6a9e0be2a32a788de5a3def330e9c585d86edd1",
        ),
    ),
)

MODEL_CATALOG: Final[dict[str, ModelOption]] = {
    option.model_id: option for option in (GEMMA_4_E2B, GEMMA_4_E4B)
}
DEFAULT_MODEL_ID: Final = GEMMA_4_E2B.model_id


def get_model_option(model_id: str) -> ModelOption:
    """Return the pinned option for `model_id`.

    Raises `KeyError` for an identifier outside the catalog; settings validation keeps configured
    values inside it.
    """
    return MODEL_CATALOG[model_id]
