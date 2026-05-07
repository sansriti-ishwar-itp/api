"""VM state machine used by DR orchestration.

Transitions are explicit; anything else is rejected.
"""

from __future__ import annotations

from enum import Enum


class VMState(str, Enum):
    HEALTHY = "healthy"
    SUSPECT = "suspect"
    FAILING = "failing"
    SNAPSHOTTING = "snapshotting"
    MIGRATING = "migrating"
    RESTORING = "restoring"
    RECOVERED = "recovered"
    FAILED = "failed"


# Allowed transitions. Anything not listed is rejected.
TRANSITIONS: dict[VMState, list[VMState]] = {
    VMState.HEALTHY: [VMState.SUSPECT, VMState.FAILING],
    VMState.SUSPECT: [VMState.HEALTHY, VMState.FAILING],
    VMState.FAILING: [VMState.SNAPSHOTTING, VMState.FAILED],
    VMState.SNAPSHOTTING: [VMState.MIGRATING, VMState.FAILED],
    VMState.MIGRATING: [VMState.RESTORING, VMState.FAILED],
    VMState.RESTORING: [VMState.RECOVERED, VMState.FAILED],
    VMState.RECOVERED: [VMState.HEALTHY],
    VMState.FAILED: [],  # Terminal: human intervention required.
}


# States that indicate a DR pipeline is in-flight.
IN_FLIGHT_STATES: frozenset[VMState] = frozenset(
    {VMState.SNAPSHOTTING, VMState.MIGRATING, VMState.RESTORING}
)


class StateMachineError(Exception):
    """Raised when a caller attempts an illegal state transition."""

    def __init__(self, current: VMState, target: VMState) -> None:
        super().__init__(f"Illegal transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def transition(current: VMState, target: VMState) -> VMState:
    """Validate and return the target state, or raise `StateMachineError`."""
    allowed = TRANSITIONS.get(current, [])
    if target not in allowed:
        raise StateMachineError(current, target)
    return target


def is_terminal(state: VMState) -> bool:
    return state == VMState.FAILED
