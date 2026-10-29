"""Configuration and workspace safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.errors import ApiError
from app.services.workspaces import (
    acquire_project_operation,
    create_project_workspace,
    ensure_within_root,
    reconcile_project_operation_reservations,
    reconcile_staged_workspace_trash,
    release_project_operation,
    sanitize_display_filename,
    stage_project_workspace_removal,
)


def test_compose_aliases_are_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep documented Compose names aligned with validated internal settings."""
    monkeypatch.setenv("NARRASTEP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NARRASTEP_MAX_UPLOAD_SIZE_MB", "55")
    monkeypatch.setenv("NARRASTEP_ALLOWED_VIDEO_EXTENSIONS", "mp4, .mkv")
    monkeypatch.setenv("NARRASTEP_CORS_ORIGINS", "http://localhost:4200,http://127.0.0.1:4200")
    monkeypatch.setenv("NARRASTEP_FRAME_DUPLICATE_THRESHOLD", "0.88")
    monkeypatch.setenv("NARRASTEP_CHATGPT_MAX_FILES_PER_BATCH", "17")
    monkeypatch.setenv("NARRASTEP_RECORDING_MAX_CHUNK_MB", "8")
    monkeypatch.setenv("NARRASTEP_RECORDING_INTERRUPTED_AFTER_SECONDS", "45")
    settings = Settings()

    assert settings.data_root == tmp_path.resolve()
    assert settings.max_upload_mb == 55
    assert settings.allowed_video_extensions == [".mp4", ".mkv"]
    assert settings.allowed_origins == ["http://localhost:4200", "http://127.0.0.1:4200"]
    assert settings.duplicate_similarity_threshold == pytest.approx(0.88)
    assert settings.chatgpt_max_files_per_batch == 17
    assert settings.recording_max_chunk_bytes == 8 * 1024 * 1024
    assert settings.recording_interrupted_after_seconds == 45


def test_workspace_path_rejects_traversal(tmp_path: Path) -> None:
    """Prevent persisted or user-derived paths from escaping the data root."""
    with pytest.raises(ApiError) as captured:
        ensure_within_root(tmp_path, "../outside.txt")

    assert captured.value.code == "INVALID_ASSET_PATH"


def test_filename_sanitization_removes_path_and_control_syntax() -> None:
    """Retain a useful display name without trusting client path components."""
    assert sanitize_display_filename(r"..\private\récording<script>.mp4") == "recording_script_.mp4"
    assert sanitize_display_filename("...") == "video"


def test_project_workspace_uses_stable_evidence_directories(tmp_path: Path) -> None:
    """Create the documented source, audio, transcript, screenshot, and export layout."""
    project_id = "12345678-1234-1234-1234-123456789abc"

    workspace = create_project_workspace(tmp_path, project_id)

    assert {path.name for path in workspace.iterdir()} == {
        "source",
        "audio",
        "diagnostics",
        "transcript",
        "screenshots",
        "exports",
    }


def test_configuration_enforces_local_private_runtime_invariants(tmp_path: Path) -> None:
    """Reject unsupported database, GPU, offline, and non-test mock configurations."""
    settings = Settings(data_root=tmp_path)
    assert settings.api_host == "127.0.0.1"
    assert settings.whisper_device == "cpu"
    assert settings.whisper_local_files_only is True
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, environment="production", mock_processing=True)
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, database_url="postgresql://local/narrastep")
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, whisper_local_files_only=False)
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, whisper_device="cuda")
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, whisper_compute_type="float16")
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, redis_url="http://localhost:6379")
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, allowed_video_extensions=[])
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, allowed_video_extensions=[".avi"])
    with pytest.raises(ValueError):
        Settings(data_root=tmp_path, chatgpt_max_files_per_batch=1)
    with pytest.raises(ValueError):
        Settings(
            data_root=tmp_path,
            adaptive_borderline_duplicate_threshold=0.95,
            adaptive_direct_duplicate_threshold=0.90,
        )
    assert settings.screenshot_selection_strategy == "legacy"
    assert settings.ocr_mode == "selective"
    assert settings.image_budget_profile == "balanced"
    assert settings.screenshot_alternatives == 2
    assert settings.orphaned_job_seconds == 300
    assert settings.job_visibility_timeout_seconds == 86400


def test_model_name_derives_matching_native_path(tmp_path: Path) -> None:
    """Keep the allow-listed model identifier and default local directory synchronized."""
    settings = Settings(data_root=tmp_path, whisper_model="tiny")
    assert settings.whisper_model == "tiny"
    assert settings.whisper_model_path.name == "tiny"


def test_workspace_trash_and_operation_reservations_recover_after_restart(tmp_path: Path) -> None:
    """Restore pre-commit deletion staging and clear abandoned upload ownership safely."""
    project_id = "12345678-1234-1234-1234-123456789abc"
    workspace = create_project_workspace(tmp_path, project_id)
    evidence = workspace / "source" / "source.mp4"
    evidence.write_bytes(b"evidence")
    staged = stage_project_workspace_removal(tmp_path, project_id)
    assert staged is not None and staged.exists()

    reconcile_staged_workspace_trash(tmp_path, {project_id})
    assert evidence.exists()

    reservation = acquire_project_operation(tmp_path, project_id, "upload")
    partial = workspace / "source" / f".upload-{reservation.token}.partial.mp4"
    partial.write_bytes(b"partial")
    reconcile_project_operation_reservations(tmp_path, {project_id})
    assert not reservation.lock_path.exists() and not partial.exists()
    release_project_operation(reservation)


def test_model_downloader_uses_the_selected_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Download tiny for explicit CI use while keeping small as the production default."""
    from scripts import download_model as downloader

    repositories: list[str] = []

    def fake_snapshot_download(*, repo_id: str, local_dir: Path) -> None:
        """Record the selected public repository without performing network access."""
        repositories.append(repo_id)
        assert local_dir == tmp_path

    monkeypatch.setattr(downloader, "snapshot_download", fake_snapshot_download)
    assert downloader.download_model(tmp_path, "tiny") == tmp_path
    assert repositories == ["Systran/faster-whisper-tiny"]
    with pytest.raises(ValueError):
        downloader.download_model(tmp_path, "unsupported")
