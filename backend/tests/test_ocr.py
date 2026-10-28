"""Safe subprocess and privacy tests for selective local OCR."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services.ocr import TesseractOcrEngine
from app.services.pipeline import PipelineServiceError


def test_tesseract_uses_argument_arrays_and_normalizes_tsv_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Invoke Tesseract without a shell and keep only normalized in-memory tokens."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Return deterministic language and TSV output for each safe command array."""
        calls.append((arguments, kwargs))
        if arguments[-1] == "--list-langs":
            return subprocess.CompletedProcess(
                arguments, 0, "List of available languages\neng\nita\n", ""
            )
        tsv = "level\ttext\n5\tAccount\n5\tÉRROR!\n5\tx\n"
        return subprocess.CompletedProcess(arguments, 0, tsv, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"local")
    engine = TesseractOcrEngine(executable="tesseract-local")

    assert engine.resolve_language("it") == ("ita", False)
    assert engine.resolve_language("it-IT") == ("ita", False)
    assert engine.resolve_language("de") == ("eng", True)
    assert engine.recognize_tokens(image_path, "ita") == frozenset({"account", "érror"})
    assert calls[-1][0] == [
        "tesseract-local",
        str(image_path),
        "stdout",
        "-l",
        "ita",
        "tsv",
    ]
    assert all(kwargs.get("shell") is None for _, kwargs in calls)


def test_tesseract_failure_exposes_only_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide executable output and OCR text when the local process fails."""

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Return a failure containing content that must never reach the public exception."""
        return subprocess.CompletedProcess(arguments, 1, "secret OCR text", "private path")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(PipelineServiceError) as captured:
        TesseractOcrEngine().available_languages()

    assert captured.value.code == "OCR_UNAVAILABLE"
    assert "secret" not in captured.value.message
    assert "path" not in captured.value.message
