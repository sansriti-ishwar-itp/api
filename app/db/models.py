"""SQLAlchemy ORM models for the DR control plane.

Persistence requirements driven by the API contract:
- `VM`: registered VMs and their current state (the state-machine row).
- `DRJob`: one row per DR pipeline invocation; supports idempotent triggers
  and lets `GET /v1/dr/jobs/{id}` return progress + SLA remaining.
- `AuditEvent`: append-only event log; written in the same transaction as the
  state mutation so partial states are impossible.
- `Snapshot`: lightweight record of snapshots created either on-demand or
  during DR. The actual blob lives in Glance/Cinder.
- `HealthCheck`: history of probes for `GET /v1/vms/{id}/health-history`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.state_machine import VMState
from app.db.session import Base


_VMStateEnum = Enum(
    VMState,
    name="vm_state",
    values_callable=lambda enum_cls: [e.value for e in enum_cls],
    native_enum=False,
)


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # External handle returned by the adapter (mock or OpenStack server id).
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[VMState] = mapped_column(
        _VMStateEnum, default=VMState.HEALTHY, nullable=False
    )
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    standby_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rto_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="vm", cascade="all, delete-orphan"
    )
    dr_jobs: Mapped[list["DRJob"]] = relationship(
        back_populates="vm", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["Snapshot"]] = relationship(
        back_populates="vm", cascade="all, delete-orphan"
    )
    health_checks: Mapped[list["HealthCheck"]] = relationship(
        back_populates="vm", cascade="all, delete-orphan"
    )


class DRJob(Base):
    __tablename__ = "dr_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    vm_id: Mapped[str] = mapped_column(ForeignKey("vms.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    # `pending` | `running` | `completed` | `failed` | `aborted`
    current_state: Mapped[VMState] = mapped_column(_VMStateEnum, default=VMState.FAILING, nullable=False)
    rto_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    new_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    abort_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    vm: Mapped[VM] = relationship(back_populates="dr_jobs")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    vm_id: Mapped[str | None] = mapped_column(
        ForeignKey("vms.id"), index=True, nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    vm: Mapped[VM | None] = relationship(back_populates="audit_events")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    vm_id: Mapped[str] = mapped_column(ForeignKey("vms.id"), index=True, nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="on-demand", nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    vm: Mapped[VM] = relationship(back_populates="snapshots")


class HealthCheck(Base):
    __tablename__ = "health_checks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    vm_id: Mapped[str] = mapped_column(ForeignKey("vms.id"), index=True, nullable=False)
    healthy: Mapped[bool] = mapped_column(nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    vm: Mapped[VM] = relationship(back_populates="health_checks")
