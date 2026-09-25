"""Structural checks of the lifecycle definition itself."""

from ledger.domain.state_machine import BatchLifecycle
from ledger.domain.value_objects import BatchStatus


def test_every_status_has_exactly_one_state() -> None:
    assert sorted(state.value for state in BatchLifecycle.states) == sorted(
        status.value for status in BatchStatus
    )


def test_only_approved_and_rejected_are_final() -> None:
    finals = {state.value for state in BatchLifecycle.states if state.final}

    assert finals == {BatchStatus.APPROVED.value, BatchStatus.REJECTED.value}


def test_draft_is_the_initial_state() -> None:
    initials = [state.value for state in BatchLifecycle.states if state.initial]

    assert initials == [BatchStatus.DRAFT.value]
