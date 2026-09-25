"""The `Batch` aggregate root."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from statemachine.exceptions import TransitionNotAllowed

from ledger.domain.exceptions import BatchTooLargeError, EmptyBatchError, InvalidStateTransitionError
from ledger.domain.state_machine import BatchLifecycle
from ledger.domain.value_objects import (
    MAX_ROWS_PER_BATCH,
    SYSTEM_ACTOR,
    ActorId,
    AnalysisReport,
    BatchStatus,
    RawTransactionRow,
)


class Batch(BaseModel):
    """A set of financial transactions that is analysed and then approved or rejected.

    State changes go exclusively through the lifecycle state machine; the public methods
    below are the only supported way to mutate a batch.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    id: UUID
    reference: str = Field(min_length=1, max_length=120)
    created_by: ActorId
    created_at: AwareDatetime
    rows: tuple[RawTransactionRow, ...]
    status: BatchStatus = BatchStatus.DRAFT
    analysis: AnalysisReport | None = None
    decided_by: ActorId | None = None
    decided_at: AwareDatetime | None = None
    rejection_reason: str | None = None

    @classmethod
    def register(
        cls,
        *,
        reference: str,
        created_by: str,
        rows: Sequence[RawTransactionRow],
        now: datetime,
        batch_id: UUID | None = None,
    ) -> Self:
        if not rows:
            raise EmptyBatchError()
        if len(rows) > MAX_ROWS_PER_BATCH:
            raise BatchTooLargeError(len(rows), MAX_ROWS_PER_BATCH)
        return cls(
            id=batch_id or uuid4(),
            reference=reference,
            created_by=created_by,
            created_at=now,
            rows=tuple(rows),
        )

    @property
    def is_final(self) -> bool:
        return self.status in (BatchStatus.APPROVED, BatchStatus.REJECTED)

    def start_processing(self) -> None:
        self._fire("start_processing")

    def record_analysis(self, report: AnalysisReport, *, now: datetime) -> None:
        """Close the processing phase: blocking issues reject the batch automatically."""
        if report.has_blocking_issues:
            self._fire("fail_validation")
            self.decided_by = SYSTEM_ACTOR
            self.decided_at = now
            self.rejection_reason = (
                f"Automatic rejection: {len(report.blocking_issues)} blocking issue(s) "
                "found during analysis."
            )
        else:
            self._fire("complete_processing")
        self.analysis = report

    def approve(self, *, approver: str, now: datetime) -> None:
        self._fire("approve", approver=approver)
        self.decided_by = approver
        self.decided_at = now

    def reject(self, *, reviewer: str, reason: str, now: datetime) -> None:
        self._fire("reject", reason=reason)
        self.decided_by = reviewer
        self.decided_at = now
        self.rejection_reason = reason.strip()

    def _fire(self, event: str, **kwargs: Any) -> None:
        machine = BatchLifecycle(self)
        try:
            machine.send(event, **kwargs)
        except TransitionNotAllowed as exc:
            raise InvalidStateTransitionError(self.id, self.status.value, event) from exc
