from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, RootModel


class CreateServerRequest(RootModel[dict[str, Any]]):
    """Passthrough body for `conn.compute.create_server(**attrs)` (legacy /v1/servers)."""


class RegisterVMRequest(BaseModel):
    """Register an existing OpenStack server for DR monitoring."""

    name: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=128)
    rto_minutes: int = Field(default=15, ge=1, le=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerDRRequest(BaseModel):
    rto_minutes: int | None = Field(default=None, ge=1, le=240)
    reason: str | None = Field(default=None, max_length=500)


class SnapshotRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default="on-demand", max_length=64)
