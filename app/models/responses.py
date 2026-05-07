from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CreateServerResponse(BaseModel):
    server_id: str
    status: str | None = None


class VmActionResponse(BaseModel):
    server_id: str
    action: str


class HealthCheckResult(BaseModel):
    healthy: bool
    latency_ms: int | None = None
    detail: str | None = None
    checked_at: datetime


class HealthCheckRecord(BaseModel):
    id: str
    healthy: bool
    latency_ms: int | None = None
    detail: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VMSummary(BaseModel):
    id: str
    external_id: str
    name: str
    state: str
    failure_count: int
    rto_minutes: int
    standby_node: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VMDetail(VMSummary):
    metadata: dict[str, Any] = {}


class DRJobResponse(BaseModel):
    id: str
    vm_id: str
    status: str
    current_state: str
    rto_minutes: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = None
    rto_remaining_seconds: float | None = None
    rto_breached: bool | None = None
    snapshot_id: str | None = None
    new_external_id: str | None = None
    error: str | None = None


class SnapshotResponse(BaseModel):
    id: str
    vm_id: str
    external_id: str
    reason: str
    job_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditEventResponse(BaseModel):
    id: str
    vm_id: str | None = None
    job_id: str | None = None
    action: str
    level: str
    message: str
    from_state: str | None = None
    to_state: str | None = None
    request_id: str | None = None
    actor: str | None = None
    payload: dict[str, Any] = {}
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
