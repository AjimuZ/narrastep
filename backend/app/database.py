"""SQLAlchemy engine and session lifecycle for the SQLite data store."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    """Provide the declarative base shared by every persisted entity."""


def configure_sqlite_connection(dbapi_connection: object, _: object) -> None:
    """Enable durability, referential integrity, and bounded lock waiting in SQLite."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def build_engine(settings: Settings | None = None) -> Engine:
    """Build a database engine from validated application settings."""
    active_settings = settings or get_settings()
    database_url = active_settings.resolved_database_url
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", configure_sqlite_connection)
    return engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_session() -> Generator[Session, None, None]:
    """Yield one transaction-aware session for a request and always close it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
