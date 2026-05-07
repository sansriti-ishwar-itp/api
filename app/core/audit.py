"""AuditService: append-only event writer for the DR control plane.

Every state transition and every API mutation goes through `record()` so:
- Audit log queries (`/v1/audit`, `/v1/vms/{id}/audit`) have a single source.
- Logs and DB rows share the same payload shape.
- Tests can assert on rows without scraping log output.

Writes are flushed in the same `AsyncSession` as the caller's other writes;
we never call `commit()` here so atomicity stays with the orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_machine import VMState
from app.db.models import AuditEvent

logger = logging.getLogger("app.audit")


def _state_value(state: VMState | str | None) -> str | None:
    if state is None:
        return None
    return state.value if isinstance(state, VMState) else str(state)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        action: str,
        message: str,
        *,
        vm_id: str | None = None,
        job_id: str | None = None,
        from_state: VMState | str | None = None,
        to_state: VMState | str | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            vm_id=vm_id,
            job_id=job_id,
            action=action,
            level=level,
            message=message,
            from_state=_state_value(from_state),
            to_state=_state_value(to_state),
            request_id=request_id,
            actor=actor,
            payload=payload or {},
        )
        self._session.add(event)
        # No commit here - the caller controls the transaction.
        logger.info(
            "audit.event",
            extra={
                "action": action,
                "vm_id": vm_id,
                "job_id": job_id,
                "from_state": event.from_state,
                "to_state": event.to_state,
                "request_id": request_id,
                "level": level,
            },
        )
        return event
