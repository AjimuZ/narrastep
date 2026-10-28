"""Stable API errors and exception handlers with non-sensitive messages."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import ErrorDetail, ErrorEnvelope

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Carry a stable machine code, safe message, and HTTP status across layers."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a public error without attaching internal exception context."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def error_response(error: ApiError) -> JSONResponse:
    """Serialize one application error using the shared camel-case envelope."""
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=error.code, message=error.message, details=error.details or {})
    )
    return JSONResponse(status_code=error.status_code, content=envelope.model_dump(by_alias=True))


async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
    """Translate a known business failure into its stable HTTP representation."""
    return error_response(error)


async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    """Return concise field validation details without echoing submitted values."""
    fields = [
        {"location": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]
    return error_response(
        ApiError("VALIDATION_ERROR", "The request is not valid.", 422, {"fields": fields})
    )


async def handle_http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
    """Normalize framework routing and method errors under the application envelope."""
    code = "RESOURCE_NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR"
    message = (
        "The requested resource was not found." if error.status_code == 404 else str(error.detail)
    )
    return error_response(ApiError(code, message, error.status_code))


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """Log unexpected failures internally and return a non-sensitive stable response."""
    logger.error(
        "Unhandled API failure for %s %s (%s).",
        request.method,
        request.url.path,
        type(error).__name__,
    )
    return error_response(
        ApiError("INTERNAL_ERROR", "Narrastep could not complete the request.", 500)
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the application error contract once during API construction."""
    app.add_exception_handler(ApiError, handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected_error)
