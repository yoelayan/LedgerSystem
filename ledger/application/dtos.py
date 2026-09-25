"""Input commands and output DTOs of the use cases (Pydantic v2)."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ledger.domain.entities import Batch
from ledger.domain.value_objects import ActorId, AnalysisReport, BatchStatus

_COMMAND_CONFIG = ConfigDict(frozen=True, extra="forbid")


class RegisterBatchCommand(BaseModel):
    model_config = _COMMAND_CONFIG

    reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    submitted_by: ActorId
    content: bytes = Field(repr=False)


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
            analysis=batch.analysis,
            decided_by=batch.decided_by,
            decided_at=batch.decided_at,
            rejection_reason=batch.rejection_reason,
        )
