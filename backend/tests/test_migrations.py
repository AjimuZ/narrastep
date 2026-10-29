"""Verify that immutable Alembic revisions reproduce the reviewed ORM schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.config import get_settings
from app.models import OutputMode, canonical_output_mode

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_TABLES = {
    "projects",
    "media_assets",
    "transcript_segments",
    "screenshots",
    "segment_screenshot_associations",
    "processing_jobs",
    "project_exports",
    "segment_lineage",
}


def test_initial_revision_matches_metadata_and_round_trips(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Upgrade an empty database, reject schema drift, downgrade, and upgrade again."""
    data_root = tmp_path / "migration-data"
    database_path = data_root / "narrastep.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("NARRASTEP_DATA_DIR", str(data_root))
    monkeypatch.setenv("NARRASTEP_DATABASE_URL", database_url)
    get_settings.cache_clear()

    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))

    try:
        command.upgrade(alembic_config, "head")

        engine = create_engine(database_url)
        schema = inspect(engine)
        assert APPLICATION_TABLES.issubset(schema.get_table_names())
        assert {item["name"] for item in schema.get_check_constraints("projects")} == {
            "output_mode",
            "project_state",
        }
        assert {item["name"] for item in schema.get_check_constraints("processing_jobs")} == {
            "job_status"
        }
        assert {item["name"] for item in schema.get_check_constraints("project_exports")} == {
            "export_status"
        }

        partial_indexes = {
            item["name"]: item
            for table_name in (
                "segment_screenshot_associations",
                "processing_jobs",
                "project_exports",
            )
            for item in schema.get_indexes(table_name)
            if item["name"].startswith("uq_")
        }
        assert set(partial_indexes) == {
            "uq_primary_screenshot_per_segment",
            "uq_active_processing_job_per_project",
            "uq_active_export_per_project",
        }
        assert all(item["unique"] == 1 for item in partial_indexes.values())
        assert all("sqlite_where" in item["dialect_options"] for item in partial_indexes.values())
        assert (
            str(
                partial_indexes["uq_primary_screenshot_per_segment"]["dialect_options"][
                    "sqlite_where"
                ]
            )
            == "is_primary = 1"
        )
        assert (
            str(
                partial_indexes["uq_active_processing_job_per_project"]["dialect_options"][
                    "sqlite_where"
                ]
            )
            == "status IN ('queued', 'processing', 'cancelling')"
        )
        assert (
            str(partial_indexes["uq_active_export_per_project"]["dialect_options"]["sqlite_where"])
            == "status IN ('queued', 'processing')"
        )
        engine.dispose()

        command.check(alembic_config)
        command.downgrade(alembic_config, "base")

        engine = create_engine(database_url)
        assert APPLICATION_TABLES.isdisjoint(inspect(engine).get_table_names())
        engine.dispose()

        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        assert APPLICATION_TABLES.issubset(inspect(engine).get_table_names())
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_adaptive_revision_backfills_existing_png_without_rewriting_path(
    tmp_path: Path, monkeypatch
) -> None:
    """Upgrade a legacy PNG row to `png_ready` while preserving its exact media reference."""
    data_root = tmp_path / "migration-backfill"
    database_path = data_root / "narrastep.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("NARRASTEP_DATA_DIR", str(data_root))
    monkeypatch.setenv("NARRASTEP_DATABASE_URL", database_url)
    get_settings.cache_clear()
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))

    try:
        command.upgrade(alembic_config, "20260715_0001")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO projects (
                        id, name, description, source_language, detected_language,
                        output_mode, state, created_at, updated_at
                    ) VALUES (
                        'project', 'Legacy', NULL, 'en', 'en',
                        'tutorial', 'review', '2026-07-20', '2026-07-20'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO screenshots (
                        id, project_id, relative_path, timestamp_seconds, perceptual_hash,
                        quality_score, width, height, created_at
                    ) VALUES (
                        'screenshot', 'project', 'projects/project/screenshots/frame.png', 1.0,
                        '0123456789abcdef', 0.9, 1280, 720, '2026-07-20'
                    )
                    """
                )
            )
        engine.dispose()

        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT relative_path, thumbnail_relative_path,
                           materialization_state, selection_reason
                    FROM screenshots WHERE id = 'screenshot'
                    """
                )
            ).one()
        engine.dispose()

        assert row.relative_path == "projects/project/screenshots/frame.png"
        assert row.thumbnail_relative_path is None
        assert row.materialization_state == "png_ready"
        assert row.selection_reason == "legacy"
    finally:
        get_settings.cache_clear()


def test_project_type_revision_migrates_version_0_2_modes(tmp_path: Path, monkeypatch) -> None:
    """Upgrade a Version 0.2 database to the ADR 0009 project types and downgrade it again.

    `technical_walkthrough` becomes `task_briefing`, other modes keep their value, child rows
    survive the SQLite table rebuild, and the new CHECK constraint rejects the retired value.
    """
    data_root = tmp_path / "migration-project-types"
    database_path = data_root / "narrastep.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("NARRASTEP_DATA_DIR", str(data_root))
    monkeypatch.setenv("NARRASTEP_DATABASE_URL", database_url)
    get_settings.cache_clear()
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    legacy_modes = {
        "tutorial-project": "tutorial",
        "meeting-project": "meeting",
        "walkthrough-project": "technical_walkthrough",
        "general-project": "general",
    }

    def project_modes() -> dict[str, str]:
        """Read every project's stored mode through a short-lived connection."""
        engine = create_engine(database_url)
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT id, output_mode FROM projects")).all()
        engine.dispose()
        return {row.id: row.output_mode for row in rows}

    try:
        command.upgrade(alembic_config, "20260720_0002")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            for project_id, mode in legacy_modes.items():
                connection.execute(
                    text(
                        """
                        INSERT INTO projects (
                            id, name, description, source_language, detected_language,
                            output_mode, state, created_at, updated_at
                        ) VALUES (
                            :id, 'Legacy', NULL, 'en', 'en',
                            :mode, 'review', '2026-07-20', '2026-07-20'
                        )
                        """
                    ),
                    {"id": project_id, "mode": mode},
                )
            connection.execute(
                text(
                    """
                    INSERT INTO processing_jobs (
                        id, project_id, status, stage, message, progress_percent,
                        progress_version, cancel_requested, created_at, updated_at,
                        configuration_snapshot
                    ) VALUES (
                        'job', 'walkthrough-project', 'completed', 'completed', 'Done', 100,
                        1, 0, '2026-07-20', '2026-07-20',
                        '{"projectMode": "technical_walkthrough"}'
                    )
                    """
                )
            )
        engine.dispose()

        command.upgrade(alembic_config, "head")
        assert project_modes() == {
            "tutorial-project": "tutorial",
            "meeting-project": "meeting",
            "walkthrough-project": "task_briefing",
            "general-project": "general",
        }
        engine = create_engine(database_url)
        with engine.connect() as connection:
            job = connection.execute(
                text("SELECT project_id, configuration_snapshot FROM processing_jobs")
            ).one()
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        constraints = {
            item["name"]: item["sqltext"]
            for item in inspect(engine).get_check_constraints("projects")
        }
        engine.dispose()
        # The immutable job snapshot keeps its historical value; processing normalizes it.
        assert job.project_id == "walkthrough-project"
        assert "technical_walkthrough" in job.configuration_snapshot
        assert set(constraints) == {"output_mode", "project_state"}
        assert "task_briefing" in constraints["output_mode"]
        assert "technical_walkthrough" not in constraints["output_mode"]
        command.check(alembic_config)

        engine = create_engine(database_url)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE projects SET output_mode = 'technical_walkthrough'"))
        engine.dispose()

        command.downgrade(alembic_config, "20260720_0002")
        assert project_modes() == legacy_modes
    finally:
        get_settings.cache_clear()


def test_legacy_mode_values_map_to_project_types() -> None:
    """Normalize the retired value found in old snapshots and keep every other value."""
    assert canonical_output_mode("technical_walkthrough") == OutputMode.TASK_BRIEFING.value
    for project_type in OutputMode:
        assert canonical_output_mode(project_type.value) == project_type.value
    assert canonical_output_mode("unknown") == "unknown"
