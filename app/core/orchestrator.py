"""DR orchestrator.

Runs: FAILING -> SNAPSHOTTING -> MIGRATING -> RESTORING -> RECOVERED.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import AdapterError, VMAdapter
from app.core.audit import AuditService
from app.core.retry import with_retry
from app.core.sla import SLATracker
from app.core.state_machine import VMState, transition
from app.db.models import VM, DRJob, Snapshot

logger = logging.getLogger("app.orchestrator")


class DROrchestrator:
    """Coordinates the multi-step DR pipeline for one VM."""

    def __init__(
        self,
        vm_id: str,
        job_id: str,
        adapter: VMAdapter,
        sessionmaker: async_sessionmaker[AsyncSession],
        rto_minutes: int = 15,
        request_id: str | None = None,
    ) -> None:
        self.vm_id = vm_id
        self.job_id = job_id
        self.adapter = adapter
        self._sm = sessionmaker
        self.sla = SLATracker(rto_minutes=rto_minutes)
        self.request_id = request_id

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        async with self._sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _check_aborted(self) -> bool:
        async with self._sm() as session:
            job = await session.get(DRJob, self.job_id)
            return bool(job and job.abort_requested)

    async def _transition(
        self,
        session: AsyncSession,
        target: VMState,
        action: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        vm = await session.get(VM, self.vm_id)
        if vm is None:
            raise AdapterError(f"VM '{self.vm_id}' was deleted mid-pipeline")
        old = vm.state
        vm.state = transition(vm.state, target)

        job = await session.get(DRJob, self.job_id)
        if job is not None:
            job.current_state = vm.state

        audit = AuditService(session)
        await audit.record(
            action=action,
            message=message,
            vm_id=self.vm_id,
            job_id=self.job_id,
            from_state=old,
            to_state=vm.state,
            request_id=self.request_id,
            payload=payload or {},
        )

    async def _mark_running(self) -> None:
        async with self._session() as session:
            job = await session.get(DRJob, self.job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)

    async def _mark_completed(
        self, snapshot_id: str, new_external_id: str, elapsed_seconds: float
    ) -> None:
        async with self._session() as session:
            job = await session.get(DRJob, self.job_id)
            if job is None:
                return
            job.status = "completed"
            job.snapshot_id = snapshot_id
            job.new_external_id = new_external_id
            job.elapsed_seconds = elapsed_seconds
            job.finished_at = datetime.now(timezone.utc)

    async def _mark_failed(self, error: str) -> None:
        # Separate transaction so we record the failure even if a step rolled back.
        async with self._session() as session:
            job = await session.get(DRJob, self.job_id)
            if job is not None:
                job.status = "failed"
                job.error = error
                job.finished_at = datetime.now(timezone.utc)
                job.elapsed_seconds = self.sla.elapsed_seconds()
            vm = await session.get(VM, self.vm_id)
            if vm is not None and vm.state != VMState.FAILED:
                # Force terminal state regardless of where we crashed.
                old = vm.state
                vm.state = VMState.FAILED
                audit = AuditService(session)
                await audit.record(
                    action="dr.failed",
                    message=f"DR pipeline failed: {error}",
                    vm_id=self.vm_id,
                    job_id=self.job_id,
                    from_state=old,
                    to_state=VMState.FAILED,
                    request_id=self.request_id,
                    level="ERROR",
                )

    async def _mark_aborted(self) -> None:
        async with self._session() as session:
            job = await session.get(DRJob, self.job_id)
            if job is None:
                return
            job.status = "aborted"
            job.finished_at = datetime.now(timezone.utc)
            job.elapsed_seconds = self.sla.elapsed_seconds()
            audit = AuditService(session)
            await audit.record(
                action="dr.aborted",
                message="DR pipeline aborted by operator",
                vm_id=self.vm_id,
                job_id=self.job_id,
                request_id=self.request_id,
                level="WARNING",
            )

    async def _step(
        self,
        target: VMState,
        action: str,
        message: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        retry_label: str,
        max_attempts: int = 3,
    ) -> Any:
        if await self._check_aborted():
            await self._mark_aborted()
            raise AdapterError("aborted")

        async with self._session() as session:
            await self._transition(session, target, action, message)

        return await with_retry(
            operation,
            label=retry_label,
            max_attempts=max_attempts,
            retry_on=(AdapterError, Exception),
        )

    async def run(self) -> dict[str, Any]:
        """Execute the DR pipeline. Returns the terminal job summary."""
        self.sla.start()
        await self._mark_running()

        # Move VM to FAILING (entry condition for the pipeline).
        try:
            async with self._session() as session:
                vm = await session.get(VM, self.vm_id)
                if vm and vm.state not in (VMState.FAILING,):
                    await self._transition(
                        session,
                        VMState.FAILING,
                        action="dr.start",
                        message="DR pipeline initiated",
                    )
        except Exception as exc:  # noqa: BLE001
            self.sla.stop()
            await self._mark_failed(f"entry transition failed: {exc}")
            return {"status": "failed", "error": str(exc)}

        snapshot_id: str | None = None
        new_external_id: str | None = None

        try:
            # Step 1: Snapshot
            snapshot_id = await self._step(
                target=VMState.SNAPSHOTTING,
                action="dr.snapshot",
                message="Creating Glance/Cinder snapshot",
                operation=self._do_snapshot,
                retry_label="snapshot",
            )

            # Persist the snapshot row in its own transaction.
            async with self._session() as session:
                vm = await session.get(VM, self.vm_id)
                if vm is not None and snapshot_id:
                    session.add(
                        Snapshot(
                            vm_id=self.vm_id,
                            external_id=snapshot_id,
                            reason="dr",
                            job_id=self.job_id,
                        )
                    )

            # Step 2: Stop source VM (move to MIGRATING)
            await self._step(
                target=VMState.MIGRATING,
                action="dr.stop_source",
                message="Stopping source VM",
                operation=lambda: self._stop_source(),
                retry_label="stop_source",
            )

            # Step 3: Find standby and boot from snapshot (move to RESTORING)
            standby_node = await self._step(
                target=VMState.RESTORING,
                action="dr.find_standby",
                message="Locating standby compute node",
                operation=self.adapter.find_standby_node,
                retry_label="find_standby",
            )

            booted = await with_retry(
                self.adapter.boot_from_snapshot,
                snapshot_id,
                standby_node,
                label="boot_from_snapshot",
                max_attempts=3,
            )
            new_external_id = booted.id

            # Step 4: Verify and mark RECOVERED
            health = await with_retry(
                self.adapter.ping_vm,
                new_external_id,
                label="post_boot_health",
                max_attempts=5,
            )
            if not health.healthy:
                raise AdapterError(f"post-boot health check failed: {health.detail}")

            async with self._session() as session:
                vm = await session.get(VM, self.vm_id)
                if vm is not None:
                    await self._transition(
                        session,
                        VMState.RECOVERED,
                        action="dr.recovered",
                        message=f"VM recovered on {standby_node} as {new_external_id}",
                        payload={
                            "snapshot_id": snapshot_id,
                            "new_external_id": new_external_id,
                            "standby_node": standby_node,
                            "elapsed_seconds": round(self.sla.elapsed_seconds(), 2),
                            "rto_breached": self.sla.breached(),
                        },
                    )
                    vm.external_id = new_external_id
                    vm.standby_node = standby_node

            self.sla.stop()
            await self._mark_completed(
                snapshot_id=snapshot_id or "",
                new_external_id=new_external_id or "",
                elapsed_seconds=self.sla.elapsed_seconds(),
            )
            return {
                "status": "completed",
                "snapshot_id": snapshot_id,
                "new_external_id": new_external_id,
                "standby_node": standby_node,
                "elapsed_seconds": round(self.sla.elapsed_seconds(), 2),
                "rto_breached": self.sla.breached(),
            }

        except Exception as exc:  # noqa: BLE001
            self.sla.stop()
            err = str(exc) or exc.__class__.__name__
            logger.exception("dr.failed", extra={"vm_id": self.vm_id, "job_id": self.job_id})
            await self._mark_failed(err)
            return {"status": "failed", "error": err}

    # --- helpers that need a fresh DB read for the external id ----------

    async def _vm_external_id(self) -> str:
        async with self._sm() as session:
            vm = await session.get(VM, self.vm_id)
            if vm is None:
                raise AdapterError(f"VM '{self.vm_id}' not found")
            return vm.external_id

    async def _do_snapshot(self) -> str:
        external_id = await self._vm_external_id()
        return await self.adapter.create_snapshot(external_id, name=f"dr-{self.job_id[:8]}")

    async def _stop_source(self) -> None:
        external_id = await self._vm_external_id()
        await self.adapter.stop_server(external_id)


async def list_recent_jobs(session: AsyncSession, limit: int = 50) -> list[DRJob]:
    result = await session.execute(select(DRJob).order_by(DRJob.created_at.desc()).limit(limit))
    return list(result.scalars().all())
