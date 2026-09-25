"""Explicit domain exceptions.

Every business-rule violation has its own exception type. Concrete errors inherit from
exactly one *category* (NotFound, Conflict, BusinessRuleError); the presentation layer
maps categories to HTTP status codes, so the domain stays ignorant of HTTP.

`DomainError` itself is never raised: an unmapped error is a programming bug and must
surface as a 500, not be disguised as a client error.
"""

from typing import Any, ClassVar
from uuid import UUID


class DomainError(Exception):
    """Base class for all domain errors. Carries a stable machine-readable code."""

    code: ClassVar[str] = "DOMAIN_ERROR"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


class NotFoundError(DomainError):
    """The requested aggregate does not exist."""


class ConflictError(DomainError):
    """The request conflicts with the current state of the aggregate."""


class BusinessRuleError(DomainError):
    """The request breaks a business invariant regardless of the current state."""


class BatchNotFoundError(NotFoundError):
    code = "BATCH_NOT_FOUND"

    def __init__(self, batch_id: UUID) -> None:
        super().__init__(f"Batch {batch_id} does not exist.", batch_id=str(batch_id))


class InvalidStateTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"

    def __init__(self, batch_id: UUID, current_status: str, event: str) -> None:
        super().__init__(
            f"Cannot '{event}' batch {batch_id} while it is {current_status}.",
            batch_id=str(batch_id),
            current_status=current_status,
            event=event,
        )


class SelfApprovalError(BusinessRuleError):
    """Four-eyes principle: whoever submits a batch cannot approve it."""

    code = "SELF_APPROVAL_FORBIDDEN"

    def __init__(self, batch_id: UUID, actor: str) -> None:
        super().__init__(
            f"User '{actor}' submitted batch {batch_id} and cannot approve it.",
            batch_id=str(batch_id),
            actor=actor,
        )


class RejectionReasonRequiredError(BusinessRuleError):
    code = "REJECTION_REASON_REQUIRED"

    def __init__(self, batch_id: UUID) -> None:
        super().__init__(
            f"Rejecting batch {batch_id} requires a non-empty reason.", batch_id=str(batch_id)
        )


class EmptyBatchError(BusinessRuleError):
    code = "EMPTY_BATCH"

    def __init__(self) -> None:
        super().__init__("A batch must contain at least one transaction.")


class BatchTooLargeError(BusinessRuleError):
    code = "BATCH_TOO_LARGE"

    def __init__(self, row_count: int, max_rows: int) -> None:
        super().__init__(
            f"Batch has {row_count} rows; the maximum allowed is {max_rows}.",
            row_count=row_count,
            max_rows=max_rows,
        )


class MalformedDatasetError(BusinessRuleError):
    """The uploaded file cannot be interpreted as a transaction dataset at all."""

    code = "MALFORMED_DATASET"

    def __init__(self, reason: str, **context: Any) -> None:
        super().__init__(f"Dataset is malformed: {reason}", **context)
