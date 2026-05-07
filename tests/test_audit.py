from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.audit import AuditService
from app.core.state_machine import VMState
from app.db.session import Base
from app.db.models import VM, AuditEvent


@pytest.fixture()
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def test_audit_record_writes_in_caller_transaction(db) -> None:
    sm = db
    async with sm() as session:
        vm = VM(external_id="ext", name="db-vm", state=VMState.HEALTHY)
        session.add(vm)
        await session.flush()
        audit = AuditService(session)
        await audit.record(
            action="vm.test",
            message="hello",
            vm_id=vm.id,
            from_state=VMState.HEALTHY,
            to_state=VMState.SUSPECT,
            request_id="rid-1",
            payload={"k": "v"},
        )
        await session.commit()

    async with sm() as session:
        events = (await session.execute(select(AuditEvent))).scalars().all()
        assert len(events) == 1
        e = events[0]
        assert e.action == "vm.test"
        assert e.from_state == VMState.HEALTHY.value
        assert e.to_state == VMState.SUSPECT.value
        assert e.request_id == "rid-1"
        assert e.payload == {"k": "v"}


async def test_audit_record_does_not_commit(db) -> None:
    sm = db
    async with sm() as session:
        vm = VM(external_id="ext-2", name="db-vm-2", state=VMState.HEALTHY)
        session.add(vm)
        await session.flush()
        audit = AuditService(session)
        await audit.record(action="vm.test", message="should rollback", vm_id=vm.id)
        await session.rollback()

    async with sm() as session:
        events = (await session.execute(select(AuditEvent))).scalars().all()
        assert events == []
