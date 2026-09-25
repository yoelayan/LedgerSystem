"""Input commands and output DTOs of the use cases (Pydantic v2)."""

from datetime import date, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ledger.domain.entities import Batch
from ledger.domain.value_objects import (
    ActorId,
    AnalysisReport,
    BatchStatus,
    ColumnMapping,
    RawTransactionRow,
)

_COMMAND_CONFIG = ConfigDict(frozen=True, extra="forbid")


class RegisterBatchCommand(BaseModel):
    model_config = _COMMAND_CONFIG

    reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    submitted_by: ActorId
    content: bytes = Field(repr=False)
    # Declared by the uploader: negative amounts are outflows, positive ones inflows.
    signed_amounts: bool = False


class ApproveBatchCommand(BaseModel):
    model_config = _COMMAND_CONFIG

    batch_id: UUID
    approver: ActorId


class RejectBatchCommand(BaseModel):
    model_config = _COMMAND_CONFIG

    batch_id: UUID
    reviewer: ActorId
    # "Reason must not be blank" is a domain rule, enforced (and tested) in the domain.
    reason: str = Field(max_length=1000)


class BatchDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    reference: str
    status: BatchStatus
    created_by: str
    created_at: datetime
    row_count: int
    column_mapping: ColumnMapping | None
    analysis: AnalysisReport | None
    decided_by: str | None
    decided_at: datetime | None
    rejection_reason: str | None

    @classmethod
    def from_entity(cls, batch: Batch) -> Self:
        return cls(
            id=batch.id,
            reference=batch.reference,
            status=batch.status,
            created_by=batch.created_by,
            created_at=batch.created_at,
            row_count=len(batch.rows),
            column_mapping=batch.column_mapping,
            analysis=batch.analysis,
            decided_by=batch.decided_by,
            decided_at=batch.decided_at,
            rejection_reason=batch.rejection_reason,
        )


class BatchDetailDTO(BatchDTO):
    """A batch plus its source rows, for screens that show the data itself."""

    rows: tuple[RawTransactionRow, ...]

    @classmethod
    def from_entity(cls, batch: Batch) -> Self:
        return cls.model_validate({**BatchDTO.from_entity(batch).model_dump(), "rows": batch.rows})


class TransactionQuery(BaseModel):
    """Which movements an analysis looks at. By default, those of approved batches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    currency: str | None = None
    account: str | None = None
    include_pending: bool = False  # also batches waiting for approval

    @property
    def statuses(self) -> tuple[BatchStatus, ...]:
        if self.include_pending:
            return (BatchStatus.APPROVED, BatchStatus.PENDING_APPROVAL)
        return (BatchStatus.APPROVED,)


class ReconcileCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: bytes = Field(repr=False)
    default_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "EUR"
    invert_debit_credit: bool = False
    date_tolerance_days: int = Field(default=3, ge=0, le=31)
