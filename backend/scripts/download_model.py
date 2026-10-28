"""Explicitly acquire a validated faster-whisper model during setup or image build."""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

from app.config import get_settings

SUPPORTED_MODELS = frozenset(
    {"tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"}
)


def download_model(destination: Path, model_name: str = "small") -> Path:
    """Download one allow-listed model snapshot into the configured local directory."""
    if model_name not in SUPPORTED_MODELS:
        supported = ", ".join(sorted(SUPPORTED_MODELS))
        raise ValueError(f"Unsupported Whisper model '{model_name}'. Choose one of: {supported}.")
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=f"Systran/faster-whisper-{model_name}",
        local_dir=destination,
    )
    return destination


def parse_arguments() -> argparse.Namespace:
    """Parse the optional destination used by native setup and container builds."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=sorted(SUPPORTED_MODELS), default=settings.whisper_model)
    parser.add_argument("destination", type=Path, nargs="?", default=settings.whisper_model_path)
    return parser.parse_args()


def main() -> None:
    """Download the model and print only its final non-sensitive local path."""
    arguments = parse_arguments()
    destination = download_model(arguments.destination.resolve(), arguments.model)
    print(destination)


if __name__ == "__main__":
    main()
