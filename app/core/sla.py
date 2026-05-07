"""SLA tracker for DR pipelines.

Tracks elapsed time against a Recovery Time Objective (RTO) so:
- Operators can see live remaining-time on `GET /v1/dr/jobs/{id}`.
- Audit events capture whether the pipeline beat the SLA.
- Future Phase 2 work can fan out alerts on `breached()`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SLATracker:
    rto_minutes: int = 15
    _started_at: float | None = None
    _stopped_at: float | None = None

    def start(self) -> None:
        self._started_at = time.monotonic()

    def stop(self) -> None:
        if self._started_at is not None and self._stopped_at is None:
            self._stopped_at = time.monotonic()

    @property
    def started(self) -> bool:
        return self._started_at is not None

    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._stopped_at if self._stopped_at is not None else time.monotonic()
        return max(0.0, end - self._started_at)

    def elapsed_minutes(self) -> float:
        return self.elapsed_seconds() / 60.0

    def remaining_seconds(self) -> float:
        return max(0.0, self.rto_minutes * 60 - self.elapsed_seconds())

    def remaining_minutes(self) -> float:
        return self.remaining_seconds() / 60.0

    def breached(self) -> bool:
        return self.elapsed_seconds() > self.rto_minutes * 60
