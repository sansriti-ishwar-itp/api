"""Integration tests for the DR orchestrator against an in-memory SQLite DB.

These exercise the full pipeline:
- happy path: VM ends RECOVERED, DRJob completed, audit rows for each step.
- snapshot failure: VM ends FAILED, job error captured.
- boot failure: VM ends FAILED partway through.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.mock import MockAdapter
from app.core.orchestrator import DROrchestrator
from app.core.state_machine import VMState
from app.db.session import Base
from app.db.models import VM, AuditEvent, DRJob


@pytest.fixture()
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed(sm: async_sessionmaker, *, external_id: str = "ext-1") -> tuple[str, str]:
    async with sm() as session:
        vm = VM(external_id=external_id, name="db-vm", state=VMState.HEALTHY, rto_minutes=15)
        session.add(vm)
        await session.flush()
        job = DRJob(vm_id=vm.id, status="pending", current_state=VMState.FAILING, rto_minutes=15)
        session.add(job)
        await session.commit()
        return vm.id, job.id


async def test_happy_path(db) -> None:
    sm = db
    adapter = MockAdapter(latency_ms=0)
    src = await adapter.create_server({"name": "src"})
    vm_id, job_id = await _seed(sm, external_id=src.id)

    orch = DROrchestrator(vm_id=vm_id, job_id=job_id, adapter=adapter, sessionmaker=sm)
    result = await orch.run()

    assert result["status"] == "completed"
    assert result["snapshot_id"]
    assert result["new_external_id"]

    async with sm() as session:
        vm = await session.get(VM, vm_id)
        job = await session.get(DRJob, job_id)
        assert vm.state == VMState.RECOVERED
        assert vm.external_id == result["new_external_id"]
        assert vm.standby_node == result["standby_node"]
        assert job.status == "completed"
        assert job.snapshot_id == result["snapshot_id"]
        events = (await session.execute(
            select(AuditEvent).where(AuditEvent.vm_id == vm_id).order_by(AuditEvent.created_at)
        )).scalars().all()
        actions = [e.action for e in events]
        assert "dr.start" in actions
        assert "dr.snapshot" in actions
        assert "dr.stop_source" in actions
        assert "dr.find_standby" in actions
        assert "dr.recovered" in actions


async def test_snapshot_failure_lands_in_failed(db) -> None:
    sm = db
    adapter = MockAdapter(latency_ms=0)
    src = await adapter.create_server({"name": "src-fail"})
    vm_id, job_id = await _seed(sm, external_id=src.id)

    # `fail_next_*` is one-shot; for deterministic test we replace the method
    # so every retry attempt fails.
    async def always_fail(server_id: str, *, name: str | None = None) -> str:
        raise RuntimeError("snapshot is broken")

    adapter.create_snapshot = always_fail  # type: ignore[assignment]

    orch = DROrchestrator(vm_id=vm_id, job_id=job_id, adapter=adapter, sessionmaker=sm)
    result = await orch.run()

    assert result["status"] == "failed"
    async with sm() as session:
        vm = await session.get(VM, vm_id)
        job = await session.get(DRJob, job_id)
        assert vm.state == VMState.FAILED
        assert job.status == "failed"
        assert "snapshot is broken" in (job.error or "")
        events = (await session.execute(
            select(AuditEvent).where(AuditEvent.vm_id == vm_id)
        )).scalars().all()
        assert any(e.action == "dr.failed" for e in events)


async def test_boot_failure_lands_in_failed(db) -> None:
    sm = db
    adapter = MockAdapter(latency_ms=0)
    src = await adapter.create_server({"name": "src-boot"})
    vm_id, job_id = await _seed(sm, external_id=src.id)

    async def boot_broken(*args, **kwargs):
        raise RuntimeError("boot is broken")

    adapter.boot_from_snapshot = boot_broken  # type: ignore[assignment]

    orch = DROrchestrator(vm_id=vm_id, job_id=job_id, adapter=adapter, sessionmaker=sm)
    result = await orch.run()

    assert result["status"] == "failed"
    async with sm() as session:
        vm = await session.get(VM, vm_id)
        job = await session.get(DRJob, job_id)
        assert vm.state == VMState.FAILED
        assert job.status == "failed"
        assert "boot is broken" in (job.error or "")


async def test_abort_short_circuits(db) -> None:
    sm = db
    adapter = MockAdapter(latency_ms=0)
    src = await adapter.create_server({"name": "src-abort"})
    vm_id, job_id = await _seed(sm, external_id=src.id)

    async with sm() as session:
        job = await session.get(DRJob, job_id)
        job.abort_requested = True
        await session.commit()

    orch = DROrchestrator(vm_id=vm_id, job_id=job_id, adapter=adapter, sessionmaker=sm)
    result = await orch.run()
    assert result["status"] == "failed"
    async with sm() as session:
        job = await session.get(DRJob, job_id)
        assert job.status in ("aborted", "failed")
