"""Selective offline OCR used only for screenshot comparison."""

from __future__ import annotations

import csv
import io
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.services.pipeline import PipelineServiceError

LANGUAGE_MAP = {
    "de": "deu",
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "it": "ita",
}
TOKEN_PATTERN = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(slots=True)
class TesseractOcrEngine:
    """Run a configured local Tesseract executable without retaining recognized text."""

    executable: str = "tesseract"
    timeout_seconds: int = 30
    _available_languages: set[str] | None = field(default=None, init=False, repr=False)

    def available_languages(self) -> set[str]:
        """Load installed language identifiers once for deterministic fallback behavior."""
        if self._available_languages is not None:
            return self._available_languages
        try:
            completed = subprocess.run(
                [self.executable, "--list-langs"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PipelineServiceError(
                "OCR_UNAVAILABLE", "The configured local OCR engine is unavailable."
            ) from error
        if completed.returncode != 0:
            raise PipelineServiceError(
                "OCR_UNAVAILABLE", "The configured local OCR engine is unavailable."
            )
        self._available_languages = {
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip() and " " not in line
        }
        return self._available_languages

    def resolve_language(self, detected_language: str | None) -> tuple[str, bool]:
        """Select an installed OCR language and report when English fallback was required."""
        language_code = (detected_language or "").strip().lower().replace("_", "-").split("-")[0]
        requested = LANGUAGE_MAP.get(language_code, "eng")
        available = self.available_languages()
        if requested in available:
            return requested, False
        if "eng" in available:
            return "eng", True
        raise PipelineServiceError(
            "OCR_LANGUAGE_UNAVAILABLE", "No supported local OCR language is installed."
        )

    def recognize_tokens(self, image_path: Path, language: str) -> frozenset[str]:
        """Return normalized tokens from Tesseract TSV output without exposing raw text."""
        try:
            completed = subprocess.run(
                [self.executable, str(image_path), "stdout", "-l", language, "tsv"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PipelineServiceError(
                "OCR_FAILED", "Local screenshot OCR could not complete."
            ) from error
        if completed.returncode != 0:
            raise PipelineServiceError("OCR_FAILED", "Local screenshot OCR could not complete.")
        reader = csv.DictReader(io.StringIO(completed.stdout), delimiter="\t")
        tokens: set[str] = set()
        for row in reader:
            normalized = _normalize_token(row.get("text", ""))
            if normalized:
                tokens.add(normalized)
        return frozenset(tokens)


def token_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Measure normalized OCR token overlap while treating two empty sets as equal."""
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _normalize_token(value: str) -> str:
    """Normalize one OCR token for comparison without retaining its original form."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = TOKEN_PATTERN.sub("", normalized)
    return normalized if len(normalized) >= 2 else ""
