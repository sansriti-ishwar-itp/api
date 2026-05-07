"""DR job endpoints (trigger, poll, abort)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import VMAdapter
from app.api.deps.openstack import get_vm_adapter
from app.core.audit import AuditService
from app.core.config import Settings, get_settings
from app.core.orchestrator import DROrchestrator
from app.core.state_machine import IN_FLIGHT_STATES, VMState
from app.db.models import VM, DRJob
from app.db.session import get_session, get_sessionmaker
from app.models.requests import TriggerDRRequest
from app.models.responses import DRJobResponse

router = APIRouter(prefix="/v1", tags=["dr"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _job_to_response(job: DRJob) -> DRJobResponse:
    elapsed = job.elapsed_seconds
    rto_remaining: float | None = None
    rto_breached: bool | None = None
    if elapsed is not None:
        rto_remaining = max(0.0, job.rto_minutes * 60 - elapsed)
        rto_breached = elapsed > job.rto_minutes * 60
    return DRJobResponse(
        id=job.id,
        vm_id=job.vm_id,
        status=job.status,
        current_state=job.current_state.value if hasattr(job.current_state, "value") else str(job.current_state),
        rto_minutes=job.rto_minutes,
        started_at=job.started_at,
        finished_at=job.finished_at,
        elapsed_seconds=round(elapsed, 2) if elapsed is not None else None,
        rto_remaining_seconds=round(rto_remaining, 2) if rto_remaining is not None else None,
        rto_breached=rto_breached,
        snapshot_id=job.snapshot_id,
        new_external_id=job.new_external_id,
        error=job.error,
    )


async def _run_pipeline(
    vm_id: str, job_id: str, adapter: VMAdapter, rto_minutes: int, request_id: str | None
) -> None:
    """Entrypoint for BackgroundTasks. Builds its own DB session factory."""
    sm = get_sessionmaker()
    orch = DROrchestrator(
        vm_id=vm_id,
        job_id=job_id,
        adapter=adapter,
        sessionmaker=sm,
        rto_minutes=rto_minutes,
        request_id=request_id,
    )
    await orch.run()


@router.post(
    "/vms/{vm_id}/dr/trigger",
    response_model=DRJobResponse,
    summary="Trigger a DR pipeline (idempotent)",
)
async def trigger_dr(
    vm_id: str,
    payload: TriggerDRRequest,
    request: Request,
    background: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    adapter: Annotated[VMAdapter, Depends(get_vm_adapter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    vm = await session.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{vm_id}' not found")

    # Idempotency: any non-terminal in-flight job replays.
    existing_q = await session.execute(
        select(DRJob)
        .where(DRJob.vm_id == vm_id, DRJob.status.in_(["pending", "running"]))
        .order_by(DRJob.created_at.desc())
        .limit(1)
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=_job_to_response(existing).model_dump(mode="json"),
            headers={"Idempotent-Replay": "true"},
        )

    rto = payload.rto_minutes or vm.rto_minutes or settings.default_rto_minutes
    job = DRJob(
        vm_id=vm_id,
        status="pending",
        current_state=vm.state if vm.state in IN_FLIGHT_STATES else VMState.FAILING,
        rto_minutes=rto,
        request_id=_request_id(request),
    )
    session.add(job)
    audit = AuditService(session)
    await audit.record(
        action="dr.triggered",
        message=payload.reason or "DR pipeline triggered",
        vm_id=vm_id,
        request_id=_request_id(request),
        payload={"rto_minutes": rto},
    )
    await session.commit()
    await session.refresh(job)

    # Schedule the pipeline after the response is sent.
    background.add_task(
        _run_pipeline, vm_id, job.id, adapter, rto, _request_id(request)
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=_job_to_response(job).model_dump(mode="json"),
        headers={"Location": f"/v1/dr/jobs/{job.id}"},
    )


@router.get(
    "/dr/jobs/{job_id}",
    response_model=DRJobResponse,
    summary="Poll a DR job",
)
async def get_dr_job(
    job_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DRJobResponse:
    job = await session.get(DRJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"DR job '{job_id}' not found")
    return _job_to_response(job)


@router.post(
    "/dr/jobs/{job_id}/abort",
    response_model=DRJobResponse,
    summary="Request abort of an in-flight DR job",
)
async def abort_dr_job(
    job_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DRJobResponse:
    job = await session.get(DRJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"DR job '{job_id}' not found")
    if job.status in ("completed", "failed", "aborted"):
        return _job_to_response(job)
    job.abort_requested = True
    audit = AuditService(session)
    await audit.record(
        action="dr.abort_requested",
        message="Operator requested abort of in-flight DR job",
        vm_id=job.vm_id,
        job_id=job.id,
        request_id=_request_id(request),
        level="WARNING",
    )
    await session.commit()
    await session.refresh(job)
    return _job_to_response(job)


@router.get(
    "/dr/jobs",
    response_model=list[DRJobResponse],
    summary="List recent DR jobs",
)
async def list_dr_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
) -> list[DRJobResponse]:
    result = await session.execute(
        select(DRJob).order_by(DRJob.created_at.desc()).limit(min(max(limit, 1), 200))
    )
    return [_job_to_response(j) for j in result.scalars().all()]
