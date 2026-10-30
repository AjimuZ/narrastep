"""Shared isolated SQLite and filesystem fixtures for backend tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="narrastep-tests-"))
TEST_DATABASE = TEST_DATA_ROOT / "narrastep.db"
os.environ["NARRASTEP_ENV"] = "test"
os.environ["NARRASTEP_DATA_DIR"] = str(TEST_DATA_ROOT)
os.environ["NARRASTEP_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["NARRASTEP_ENABLE_MOCK_PROCESSING"] = "true"
os.environ["NARRASTEP_CELERY_ALWAYS_EAGER"] = "false"
os.environ["NARRASTEP_MIN_FREE_DISK_MB"] = "0"

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None]:
    """Reset all persisted rows and project files before every test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    projects = TEST_DATA_ROOT / "projects"
    trash = TEST_DATA_ROOT / ".trash"
    shutil.rmtree(projects, ignore_errors=True)
    shutil.rmtree(trash, ignore_errors=True)
    shutil.rmtree(TEST_DATA_ROOT / "models", ignore_errors=True)
    yield


@pytest.fixture
def client() -> Generator[TestClient]:
    """Yield an in-process HTTP client against the isolated application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session() -> Generator[Session]:
    """Yield a direct ORM session for arranging business-rule fixtures."""
    with SessionLocal() as database_session:
        yield database_session
