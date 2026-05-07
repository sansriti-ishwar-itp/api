"""Liveness/readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters import VMAdapter, build_adapter
from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness (legacy path)")
def health_legacy() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/live", summary="Liveness probe")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe")
async def health_ready(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    if settings.adapter_mode == "mock":
        return {"status": "ready", "adapter": "mock"}

    # In openstack mode, we can only validate config without credentials.
    if not settings.openstack_auth_url:
        return {"status": "not-ready", "adapter": "openstack", "detail": "OPENSTACK_AUTH_URL missing"}
    return {"status": "ready", "adapter": "openstack"}


def get_adapter_for_health() -> VMAdapter:
    return build_adapter()
