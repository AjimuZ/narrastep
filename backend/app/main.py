"""FastAPI application factory for Narrastep's local-only HTTP service."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from swagger_ui_bundle import swagger_ui_path  # type: ignore[import-untyped]

from app import __version__
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.errors import install_error_handlers
from app.models import Project
from app.routes.api import router
from app.services.reconciliation import reconcile_interrupted_work
from app.services.workspaces import (
    reconcile_project_operation_reservations,
    reconcile_staged_workspace_trash,
)


def configure_logging(settings: Settings) -> None:
    """Configure concise operational logging without enabling transcript or payload output."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct the application with local docs, stable errors, and versioned routes."""
    active_settings = settings or get_settings()
    configure_logging(active_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        """Prepare local storage and reconcile stale work around process startup."""
        active_settings.data_root.mkdir(parents=True, exist_ok=True)
        reconcile_interrupted_work(active_settings)
        with SessionLocal() as session:
            project_ids = set(session.scalars(select(Project.id)))
        reconcile_staged_workspace_trash(active_settings.data_root, project_ids)
        reconcile_project_operation_reservations(active_settings.data_root, project_ids)
        yield

    application = FastAPI(
        title="Narrastep API",
        version=__version__,
        description="Local-first video transcription, evidence review, and export API.",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Range", "Last-Event-ID"],
        expose_headers=["Accept-Ranges", "Content-Range", "Content-Length"],
    )
    install_error_handlers(application)
    application.include_router(router)
    application.mount(
        "/api/assets/swagger-ui",
        StaticFiles(directory=swagger_ui_path),
        name="swagger-ui-assets",
    )

    @application.get("/api/docs", include_in_schema=False, response_class=HTMLResponse)
    def local_swagger_ui() -> HTMLResponse:
        """Serve interactive API documentation using only bundled local assets."""
        return get_swagger_ui_html(
            openapi_url=application.openapi_url or "/api/openapi.json",
            title="Narrastep API documentation",
            swagger_js_url="/api/assets/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/api/assets/swagger-ui/swagger-ui.css",
            swagger_favicon_url="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
            swagger_ui_parameters={"validatorUrl": None},
        )

    return application


app = create_app()
