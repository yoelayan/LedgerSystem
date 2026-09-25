"""The `Batch` aggregate root."""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from statemachine.exceptions import TransitionNotAllowed

from ledger.domain.exceptions import (
    BatchTooLargeError,
    EmptyBatchError,
    InvalidStateTransitionError,
)
from ledger.domain.state_machine import BatchLifecycle
from ledger.domain.value_objects import (
    MAX_ROWS_PER_BATCH,
    SYSTEM_ACTOR,
    ActorId,
    AnalysisReport,
    BatchStatus,
    ColumnMapping,
    Direction,
    RawTransactionRow,
    Severity,
    Transaction,
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
    column_mapping: ColumnMapping | None = None
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
        column_mapping: ColumnMapping | None = None,
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
            column_mapping=column_mapping,
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

    def valid_transactions(self) -> list[Transaction]:
        """The rows the analysis accepted, typed. Empty until the batch is analysed."""
        if self.analysis is None:
            return []
        blocked = {i.row_number for i in self.analysis.issues if i.severity is Severity.ERROR}
        return [
            Transaction(
                batch_id=self.id,
                row_number=row.row_number,
                external_id=row.external_id,
                account=row.account,
                amount=Decimal(row.amount),
                currency=row.currency.upper(),
                value_date=date.fromisoformat(row.value_date),
                direction=Direction(row.direction),
            )
            for row in self.rows
            if row.row_number not in blocked
        ]

    def _fire(self, event: str, **kwargs: Any) -> None:
        machine = BatchLifecycle(self)
        try:
            machine.send(event, **kwargs)
        except TransitionNotAllowed as exc:
            raise InvalidStateTransitionError(self.id, self.status.value, event) from exc
