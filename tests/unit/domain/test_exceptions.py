"""The exception hierarchy is part of the public contract: keep it honest."""

from uuid import uuid4

import pytest

from ledger.domain import exceptions as exc
from ledger.domain.exceptions import (
    BusinessRuleError,
    ConflictError,
    DomainError,
    NotFoundError,
)

CATEGORIES = (NotFoundError, ConflictError, BusinessRuleError)


def _concrete_errors() -> list[type[DomainError]]:
    found: list[type[DomainError]] = []
    pending: list[type[DomainError]] = [DomainError]
    while pending:
        klass = pending.pop()
        for sub in klass.__subclasses__():
            pending.append(sub)
            if sub not in CATEGORIES:
                found.append(sub)
    return found


@pytest.mark.parametrize("error_type", _concrete_errors(), ids=lambda t: t.__name__)
def test_every_concrete_error_belongs_to_exactly_one_category(
    error_type: type[DomainError],
) -> None:
    assert sum(issubclass(error_type, category) for category in CATEGORIES) == 1
    assert error_type.code != DomainError.code


def test_codes_are_unique() -> None:
    codes = [error_type.code for error_type in _concrete_errors()]

    assert len(codes) == len(set(codes))


def test_errors_carry_message_and_structured_context() -> None:
    batch_id = uuid4()
    cases: list[tuple[DomainError, dict[str, object]]] = [
        (exc.BatchNotFoundError(batch_id), {"batch_id": str(batch_id)}),
        (
            exc.SelfApprovalError(batch_id, "alice"),
            {"batch_id": str(batch_id), "actor": "alice"},
        ),
        (exc.RejectionReasonRequiredError(batch_id), {"batch_id": str(batch_id)}),
        (exc.EmptyBatchError(), {}),
        (exc.BatchTooLargeError(11, 10), {"row_count": 11, "max_rows": 10}),
        (
            exc.MalformedDatasetError("missing columns", missing_columns=["amount"]),
            {"missing_columns": ["amount"]},
        ),
    ]

    for error, expected_context in cases:
        assert error.message
        assert str(error) == error.message
        assert error.context == expected_context
