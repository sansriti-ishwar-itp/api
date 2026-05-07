"""FastAPI entrypoint for the DR control plane.

Wires routers, DB lifecycle, request IDs, logging, and metrics.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routers.audit import router as audit_router
from app.api.routers.dr import router as dr_router
from app.api.routers.health import router as health_router
from app.api.routers.servers import router as servers_router
from app.api.routers.vms import router as vms_router
from app.core.errors import ErrorCode, openstack_exception_to_http
from app.core.logging_setup import configure_logging, set_request_id
from app.db.session import create_all, init_engine, shutdown_engine

logger = logging.getLogger("app")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            set_request_id(None)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_engine()
    await create_all()
    logger.info("app.startup", extra={"event": "started"})
    try:
        yield
    finally:
        await shutdown_engine()
        logger.info("app.shutdown", extra={"event": "stopped"})


app = FastAPI(
    title="DR Orchestration Platform (OpenStack)",
    version="0.2.0",
    description=(
        "Disaster Recovery control plane for OpenStack VMs. Register VMs, run "
        "health checks, and orchestrate snapshot -> migrate -> restore "
        "pipelines with explicit state machine, audit trail, and per-job SLA."
    ),
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        body = {
            "error": {
                "code": detail["code"],
                "message": detail["message"],
                "request_id": request_id,
            }
        }
    else:
        body = {
            "error": {
                "code": _code_for_http(exc.status_code).value,
                "message": str(detail) if detail else "",
                "request_id": request_id,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": ErrorCode.BAD_REQUEST.value,
                "message": "Request validation failed",
                "request_id": request_id,
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "unhandled.exception",
        extra={"path": request.url.path, "request_id": request_id},
    )
    mapped = openstack_exception_to_http(exc)
    detail = mapped.detail if isinstance(mapped.detail, dict) else {"code": ErrorCode.INTERNAL.value, "message": str(mapped.detail)}
    return JSONResponse(
        status_code=mapped.status_code,
        content={
            "error": {
                "code": detail.get("code", ErrorCode.INTERNAL.value),
                "message": detail.get("message", "Internal server error"),
                "request_id": request_id,
            }
        },
    )


def _code_for_http(http_status: int) -> ErrorCode:
    if http_status == 404:
        return ErrorCode.NOT_FOUND
    if http_status == 400:
        return ErrorCode.BAD_REQUEST
    if http_status == 401 or http_status == 403:
        return ErrorCode.FORBIDDEN
    if http_status == 409:
        return ErrorCode.CONFLICT
    if 500 <= http_status < 600:
        return ErrorCode.INTERNAL
    return ErrorCode.BAD_REQUEST


app.add_middleware(RequestIDMiddleware)

app.include_router(health_router)
app.include_router(servers_router)
app.include_router(vms_router)
app.include_router(dr_router)
app.include_router(audit_router)

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
