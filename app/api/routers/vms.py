"""VMs tracked by the DR control plane (register, health, snapshots, audit)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import VMAdapter
from app.api.deps.openstack import get_vm_adapter
from app.core.audit import AuditService
from app.core.config import Settings, get_settings
from app.core.state_machine import VMState
from app.db.models import VM, AuditEvent, HealthCheck, Snapshot
from app.db.session import get_session
from app.models.requests import RegisterVMRequest, SnapshotRequest
from app.models.responses import (
    AuditEventResponse,
    HealthCheckResult,
    SnapshotResponse,
    VMDetail,
    VMSummary,
)

router = APIRouter(prefix="/v1/vms", tags=["vms"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("", response_model=list[VMSummary], summary="List DR-managed VMs")
async def list_vms(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VM]:
    result = await session.execute(select(VM).order_by(VM.created_at.desc()))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=VMSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Register a VM for DR monitoring",
)
async def register_vm(
    payload: RegisterVMRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VM:
    existing = await session.execute(
        select(VM).where(VM.external_id == payload.external_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"VM with external_id={payload.external_id} already registered")

    vm = VM(
        external_id=payload.external_id,
        name=payload.name,
        state=VMState.HEALTHY,
        rto_minutes=payload.rto_minutes or settings.default_rto_minutes,
        extra_metadata=payload.metadata,
    )
    session.add(vm)
    await session.flush()
    audit = AuditService(session)
    await audit.record(
        action="vm.registered",
        message=f"Registered VM '{payload.name}' for DR",
        vm_id=vm.id,
        to_state=VMState.HEALTHY,
        request_id=_request_id(request),
        payload={"external_id": payload.external_id},
    )
    await session.commit()
    await session.refresh(vm)
    return vm


@router.get("/{vm_id}", response_model=VMDetail, summary="Get VM detail")
async def get_vm(
    vm_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VMDetail:
    vm = await session.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")
    return VMDetail(
        id=vm.id,
        external_id=vm.external_id,
        name=vm.name,
        state=vm.state.value if hasattr(vm.state, "value") else str(vm.state),
        failure_count=vm.failure_count,
        rto_minutes=vm.rto_minutes,
        standby_node=vm.standby_node,
        created_at=vm.created_at,
        updated_at=vm.updated_at,
        metadata=vm.extra_metadata or {},
    )


@router.delete(
    "/{vm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Deregister a VM from DR monitoring",
)
async def deregister_vm(
    vm_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    vm = await session.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")
    audit = AuditService(session)
    await audit.record(
        action="vm.deregistered",
        message=f"Deregistered VM '{vm.name}'",
        vm_id=vm.id,
        request_id=_request_id(request),
    )
    await session.delete(vm)
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/{vm_id}/health-check",
    response_model=HealthCheckResult,
    summary="Trigger an immediate health probe",
)
async def trigger_health_check(
    vm_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    adapter: Annotated[VMAdapter, Depends(get_vm_adapter)],
) -> HealthCheckResult:
    vm = await session.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")

    result = await adapter.ping_vm(vm.external_id)

    record = HealthCheck(
        vm_id=vm.id,
        healthy=result.healthy,
        latency_ms=result.latency_ms,
        detail=result.detail,
    )
    session.add(record)

    # Update suspect/healthy state based on consecutive failures.
    audit = AuditService(session)
    old = vm.state
    if result.healthy:
        vm.failure_count = 0
        if vm.state == VMState.SUSPECT:
            vm.state = VMState.HEALTHY
            await audit.record(
                action="vm.recovered_from_suspect",
                message="Health check passed; back to HEALTHY",
                vm_id=vm.id,
                from_state=old,
                to_state=VMState.HEALTHY,
                request_id=_request_id(request),
            )
    else:
        vm.failure_count += 1
        if vm.state == VMState.HEALTHY and vm.failure_count >= 1:
            vm.state = VMState.SUSPECT
            await audit.record(
                action="vm.suspect",
                message=f"Health probe failed (count={vm.failure_count})",
                vm_id=vm.id,
                from_state=old,
                to_state=VMState.SUSPECT,
                request_id=_request_id(request),
                level="WARNING",
            )
        elif vm.state == VMState.SUSPECT and vm.failure_count >= 3:
            vm.state = VMState.FAILING
            await audit.record(
                action="vm.failing",
                message=f"Health probe failed (count={vm.failure_count}); auto-trigger threshold reached",
                vm_id=vm.id,
                from_state=old,
                to_state=VMState.FAILING,
                request_id=_request_id(request),
                level="ERROR",
            )

    await session.commit()
    return HealthCheckResult(
        healthy=result.healthy,
        latency_ms=result.latency_ms,
        detail=result.detail,
        checked_at=datetime.now(timezone.utc),
    )


@router.post(
    "/{vm_id}/snapshot",
    response_model=SnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Take an on-demand snapshot",
)
async def create_snapshot(
    vm_id: str,
    payload: SnapshotRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    adapter: Annotated[VMAdapter, Depends(get_vm_adapter)],
) -> Snapshot:
    vm = await session.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")
    snap_external = await adapter.create_snapshot(vm.external_id, name=payload.name)
    snap = Snapshot(
        vm_id=vm.id,
        external_id=snap_external,
        reason=payload.reason or "on-demand",
    )
    session.add(snap)
    audit = AuditService(session)
    await audit.record(
        action="vm.snapshot",
        message=f"Snapshot created: {snap_external}",
        vm_id=vm.id,
        request_id=_request_id(request),
        payload={"snapshot_id": snap_external, "reason": payload.reason},
    )
    await session.commit()
    await session.refresh(snap)
    return snap


@router.get(
    "/{vm_id}/snapshots",
    response_model=list[SnapshotResponse],
    summary="List snapshots for a VM",
)
async def list_snapshots(
    vm_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Snapshot]:
    if (await session.get(VM, vm_id)) is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")
    result = await session.execute(
        select(Snapshot).where(Snapshot.vm_id == vm_id).order_by(Snapshot.created_at.desc())
    )
    return list(result.scalars().all())


@router.get(
    "/{vm_id}/audit",
    response_model=list[AuditEventResponse],
    summary="Audit log for a VM",
)
async def vm_audit(
    vm_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AuditEvent]:
    if (await session.get(VM, vm_id)) is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")
    result = await session.execute(
        select(AuditEvent).where(AuditEvent.vm_id == vm_id).order_by(AuditEvent.created_at.desc())
    )
    return list(result.scalars().all())
