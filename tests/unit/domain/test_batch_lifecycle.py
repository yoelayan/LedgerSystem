"""State machine behaviour of the Batch aggregate: valid and invalid transitions."""

from collections.abc import Callable

import pytest

from ledger.domain.entities import Batch
from ledger.domain.exceptions import (
    BatchTooLargeError,
    EmptyBatchError,
    InvalidStateTransitionError,
    RejectionReasonRequiredError,
    SelfApprovalError,
)
from ledger.domain.value_objects import MAX_ROWS_PER_BATCH, SYSTEM_ACTOR, BatchStatus
from tests.factories import NOW, SUBMITTER, blocking_report, clean_report, make_batch, make_rows

S = BatchStatus


class TestRegistration:
    def test_new_batch_starts_as_draft(self) -> None:
        batch = Batch.register(
            reference="sept-payroll", created_by=SUBMITTER, rows=make_rows(), now=NOW
        )

        assert batch.status is S.DRAFT
        assert batch.created_by == SUBMITTER
        assert len(batch.rows) == 3
        assert batch.analysis is None
        assert not batch.is_final

    def test_empty_batch_is_refused(self) -> None:
        with pytest.raises(EmptyBatchError):
            Batch.register(reference="empty", created_by=SUBMITTER, rows=[], now=NOW)

    def test_oversized_batch_is_refused(self) -> None:
        rows = make_rows(MAX_ROWS_PER_BATCH + 1)

        with pytest.raises(BatchTooLargeError) as exc_info:
            Batch.register(reference="huge", created_by=SUBMITTER, rows=rows, now=NOW)

        assert exc_info.value.context == {
            "row_count": MAX_ROWS_PER_BATCH + 1,
            "max_rows": MAX_ROWS_PER_BATCH,
        }


class TestValidTransitions:
    def test_happy_path_to_approved(self) -> None:
        batch = make_batch(S.DRAFT)

        batch.start_processing()
        assert batch.status is S.PROCESSING

        batch.record_analysis(clean_report(), now=NOW)
        assert batch.status is S.PENDING_APPROVAL
        assert batch.analysis == clean_report()

        batch.approve(approver="bob", now=NOW)
        assert batch.status is S.APPROVED
        assert batch.decided_by == "bob"
        assert batch.decided_at == NOW
        assert batch.is_final

    def test_blocking_issues_reject_the_batch_automatically(self) -> None:
        batch = make_batch(S.PROCESSING)

        batch.record_analysis(blocking_report(), now=NOW)

        assert batch.status is S.REJECTED
        assert batch.decided_by == SYSTEM_ACTOR
        assert batch.rejection_reason is not None
        assert "1 blocking issue" in batch.rejection_reason
        assert batch.analysis == blocking_report()

    def test_manual_rejection_records_reviewer_and_reason(self) -> None:
        batch = make_batch(S.PENDING_APPROVAL)

        batch.reject(reviewer="bob", reason="  Amounts do not match the invoice  ", now=NOW)

        assert batch.status is S.REJECTED
        assert batch.decided_by == "bob"
        assert batch.rejection_reason == "Amounts do not match the invoice"

    def test_submitter_may_withdraw_by_rejecting(self) -> None:
        batch = make_batch(S.PENDING_APPROVAL)

        batch.reject(reviewer=SUBMITTER, reason="Uploaded the wrong file", now=NOW)

        assert batch.status is S.REJECTED


Action = Callable[[Batch], None]

ACTIONS: dict[str, Action] = {
    "start_processing": lambda b: b.start_processing(),
    "record_analysis": lambda b: b.record_analysis(clean_report(), now=NOW),
    "fail_validation": lambda b: b.record_analysis(blocking_report(), now=NOW),
    "approve": lambda b: b.approve(approver="bob", now=NOW),
    "reject": lambda b: b.reject(reviewer="bob", reason="nope", now=NOW),
}

INVALID_TRANSITIONS = [
    (S.PROCESSING, "start_processing"),
    (S.PENDING_APPROVAL, "start_processing"),
    (S.APPROVED, "start_processing"),
    (S.REJECTED, "start_processing"),
    (S.DRAFT, "record_analysis"),
    (S.PENDING_APPROVAL, "record_analysis"),
    (S.APPROVED, "record_analysis"),
    (S.REJECTED, "fail_validation"),
    (S.DRAFT, "approve"),
    (S.PROCESSING, "approve"),
    (S.APPROVED, "approve"),
    (S.REJECTED, "approve"),
    (S.DRAFT, "reject"),
    (S.PROCESSING, "reject"),
    (S.APPROVED, "reject"),
    (S.REJECTED, "reject"),
]


class TestInvalidTransitions:
    @pytest.mark.parametrize(("status", "action"), INVALID_TRANSITIONS)
    def test_is_refused_and_leaves_state_untouched(self, status: BatchStatus, action: str) -> None:
        batch = make_batch(status)
        before = batch.model_copy(deep=True)

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            ACTIONS[action](batch)

        assert batch == before
        assert exc_info.value.context["current_status"] == status.value
        assert exc_info.value.code == "INVALID_STATE_TRANSITION"

    def test_error_names_the_attempted_event(self) -> None:
        batch = make_batch(S.APPROVED)

        with pytest.raises(InvalidStateTransitionError, match="Cannot 'approve'") as exc_info:
            batch.approve(approver="carol", now=NOW)

        assert exc_info.value.context == {
            "batch_id": str(batch.id),
            "current_status": "APPROVED",
            "event": "approve",
        }


class TestGuards:
    def test_submitter_cannot_approve_own_batch(self) -> None:
        batch = make_batch(S.PENDING_APPROVAL)

        with pytest.raises(SelfApprovalError) as exc_info:
            batch.approve(approver=SUBMITTER, now=NOW)

        assert batch.status is S.PENDING_APPROVAL
        assert batch.decided_by is None
        assert exc_info.value.context["actor"] == SUBMITTER

    @pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
    def test_rejection_requires_a_reason(self, reason: str) -> None:
        batch = make_batch(S.PENDING_APPROVAL)

        with pytest.raises(RejectionReasonRequiredError):
            batch.reject(reviewer="bob", reason=reason, now=NOW)

        assert batch.status is S.PENDING_APPROVAL

    def test_state_is_checked_before_business_guards(self) -> None:
        """An already-approved batch reports the conflict, not the four-eyes rule."""
        batch = make_batch(S.APPROVED, decided_by="bob", decided_at=NOW)

        with pytest.raises(InvalidStateTransitionError):
            batch.approve(approver=SUBMITTER, now=NOW)
