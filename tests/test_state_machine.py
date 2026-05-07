from __future__ import annotations

import pytest

from app.core.state_machine import (
    IN_FLIGHT_STATES,
    TRANSITIONS,
    StateMachineError,
    VMState,
    is_terminal,
    transition,
)


def test_every_state_has_a_transition_entry() -> None:
    for state in VMState:
        assert state in TRANSITIONS, f"missing transition entry for {state}"


@pytest.mark.parametrize(
    "current,target",
    [
        (VMState.HEALTHY, VMState.SUSPECT),
        (VMState.SUSPECT, VMState.HEALTHY),
        (VMState.SUSPECT, VMState.FAILING),
        (VMState.FAILING, VMState.SNAPSHOTTING),
        (VMState.SNAPSHOTTING, VMState.MIGRATING),
        (VMState.MIGRATING, VMState.RESTORING),
        (VMState.RESTORING, VMState.RECOVERED),
        (VMState.RECOVERED, VMState.HEALTHY),
        (VMState.FAILING, VMState.FAILED),
        (VMState.SNAPSHOTTING, VMState.FAILED),
        (VMState.MIGRATING, VMState.FAILED),
        (VMState.RESTORING, VMState.FAILED),
    ],
)
def test_legal_transitions(current: VMState, target: VMState) -> None:
    assert transition(current, target) == target


@pytest.mark.parametrize(
    "current,target",
    [
        (VMState.HEALTHY, VMState.RECOVERED),
        (VMState.HEALTHY, VMState.SNAPSHOTTING),
        (VMState.RECOVERED, VMState.SNAPSHOTTING),
        (VMState.RECOVERED, VMState.FAILED),
        (VMState.FAILED, VMState.HEALTHY),
        (VMState.FAILED, VMState.RECOVERED),
        (VMState.SNAPSHOTTING, VMState.RECOVERED),
    ],
)
def test_illegal_transitions_raise(current: VMState, target: VMState) -> None:
    with pytest.raises(StateMachineError):
        transition(current, target)


def test_in_flight_states_are_pipeline_steps() -> None:
    assert VMState.SNAPSHOTTING in IN_FLIGHT_STATES
    assert VMState.MIGRATING in IN_FLIGHT_STATES
    assert VMState.RESTORING in IN_FLIGHT_STATES
    assert VMState.HEALTHY not in IN_FLIGHT_STATES
    assert VMState.RECOVERED not in IN_FLIGHT_STATES
    assert VMState.FAILED not in IN_FLIGHT_STATES


def test_failed_is_terminal() -> None:
    assert is_terminal(VMState.FAILED)
    assert TRANSITIONS[VMState.FAILED] == []
    assert not is_terminal(VMState.HEALTHY)
