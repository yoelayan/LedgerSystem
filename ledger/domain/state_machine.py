"""Batch lifecycle expressed with python-statemachine.

    DRAFT -> PROCESSING -> PENDING_APPROVAL -> APPROVED
                      \\                    \\-> REJECTED
                       \\-> REJECTED (automatic, blocking issues found)

Business guards are implemented as *validators*: they run only after the library has
confirmed that the transition exists from the current state, and any exception they raise
aborts the transition before the state is touched.
"""

from typing import TYPE_CHECKING

from statemachine import State, StateMachine

from ledger.domain.exceptions import RejectionReasonRequiredError, SelfApprovalError
from ledger.domain.value_objects import BatchStatus

if TYPE_CHECKING:
    from ledger.domain.entities import Batch


class BatchLifecycle(StateMachine):
    draft = State("Draft", value=BatchStatus.DRAFT.value, initial=True)
    processing = State("Processing", value=BatchStatus.PROCESSING.value)
    pending_approval = State("Pending approval", value=BatchStatus.PENDING_APPROVAL.value)
    approved = State("Approved", value=BatchStatus.APPROVED.value, final=True)
    rejected = State("Rejected", value=BatchStatus.REJECTED.value, final=True)

    start_processing = draft.to(processing)
    complete_processing = processing.to(pending_approval)
    fail_validation = processing.to(rejected)
    approve = pending_approval.to(approved, validators="guard_four_eyes")
    reject = pending_approval.to(rejected, validators="guard_reason_given")

    def __init__(self, batch: "Batch") -> None:
        self.batch = batch
        # The machine reads/writes `batch.status` directly; it never keeps its own copy.
        super().__init__(model=batch, state_field="status")

    def guard_four_eyes(self, approver: str) -> None:
        if approver == self.batch.created_by:
            raise SelfApprovalError(self.batch.id, approver)

    def guard_reason_given(self, reason: str) -> None:
        if not reason.strip():
            raise RejectionReasonRequiredError(self.batch.id)
