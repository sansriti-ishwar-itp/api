"""Error mapping: provider exceptions -> stable HTTP responses with `error.code`.

The HTTP `detail` is sanitized to a short message (no raw OpenStack tracebacks
leaking into the API response). The full exception is logged at WARN/ERROR
through the structured logger so operators still get the diagnostic data.
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import HTTPException, status
from openstack import exceptions as os_exc

from app.adapters.base import (
    AdapterError,
    StandbyUnavailableError,
    VMNotFoundError,
)
from app.core.state_machine import StateMachineError

logger = logging.getLogger("app.errors")


class ErrorCode(str, Enum):
    NOT_FOUND = "RESOURCE_NOT_FOUND"
    BAD_REQUEST = "BAD_REQUEST"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    ILLEGAL_STATE_TRANSITION = "ILLEGAL_STATE_TRANSITION"
    STANDBY_UNAVAILABLE = "STANDBY_UNAVAILABLE"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    INTERNAL = "INTERNAL_ERROR"


_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.NOT_FOUND: "Resource not found",
    ErrorCode.BAD_REQUEST: "Invalid request",
    ErrorCode.FORBIDDEN: "Forbidden",
    ErrorCode.CONFLICT: "Conflict",
    ErrorCode.ILLEGAL_STATE_TRANSITION: "Illegal VM state transition",
    ErrorCode.STANDBY_UNAVAILABLE: "No standby capacity available",
    ErrorCode.UPSTREAM_UNAVAILABLE: "Upstream provider unavailable",
    ErrorCode.INTERNAL: "Internal server error",
}


def _http_for(code: ErrorCode) -> int:
    return {
        ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
        ErrorCode.BAD_REQUEST: status.HTTP_400_BAD_REQUEST,
        ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
        ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
        ErrorCode.ILLEGAL_STATE_TRANSITION: status.HTTP_409_CONFLICT,
        ErrorCode.STANDBY_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
        ErrorCode.UPSTREAM_UNAVAILABLE: status.HTTP_502_BAD_GATEWAY,
        ErrorCode.INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }[code]


def _make(code: ErrorCode, provider_message: str | None = None) -> HTTPException:
    """Build an HTTPException with a stable code + a useful message.

    For 4xx errors we forward the provider message (caller-actionable). For
    5xx we use the default to avoid leaking internal info.
    """
    http_code = _http_for(code)
    if http_code >= 500 or not provider_message:
        msg = _DEFAULT_MESSAGES[code]
    else:
        msg = provider_message.strip() or _DEFAULT_MESSAGES[code]
    return HTTPException(
        status_code=http_code,
        detail={"code": code.value, "message": msg},
    )


def openstack_exception_to_http(exc: Exception) -> HTTPException:
    """Map an exception (provider-side or local) to a sanitized HTTPException."""
    logger.warning(
        "error.mapping",
        extra={"exc_type": exc.__class__.__name__, "exc_msg": str(exc)[:500]},
    )

    if isinstance(exc, StateMachineError):
        return _make(ErrorCode.ILLEGAL_STATE_TRANSITION, str(exc))
    if isinstance(exc, StandbyUnavailableError):
        return _make(ErrorCode.STANDBY_UNAVAILABLE)
    if isinstance(exc, (VMNotFoundError, os_exc.NotFoundException)):
        return _make(ErrorCode.NOT_FOUND, str(exc))
    if isinstance(exc, os_exc.BadRequestException):
        return _make(ErrorCode.BAD_REQUEST, str(exc))
    if isinstance(exc, os_exc.ForbiddenException):
        return _make(ErrorCode.FORBIDDEN, str(exc))
    if isinstance(exc, os_exc.ConflictException):
        return _make(ErrorCode.CONFLICT, str(exc))
    if isinstance(exc, AdapterError):
        return _make(ErrorCode.UPSTREAM_UNAVAILABLE)
    return _make(ErrorCode.UPSTREAM_UNAVAILABLE)
