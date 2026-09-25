"""Test data builders."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from ledger.domain.entities import Batch
from ledger.domain.value_objects import (
    AnalysisIssue,
    AnalysisReport,
    BatchStatus,
    Direction,
    IssueCode,
    RawTransactionRow,
    Severity,
    Transaction,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SUBMITTER = "alice"

CSV_HEADER = "external_id,account,amount,currency,value_date"


def make_row(row_number: int = 1, **overrides: str) -> RawTransactionRow:
    values = {
        "external_id": f"TX-{row_number}",
        "account": f"ACC-{row_number}",
        "amount": "100.00",
        "currency": "USD",
        "value_date": "2026-09-01",
    }
    values.update(overrides)
    return RawTransactionRow(row_number=row_number, **values)


def make_rows(count: int = 3) -> list[RawTransactionRow]:
    return [make_row(i) for i in range(1, count + 1)]


def clean_report(total_rows: int = 3) -> AnalysisReport:
    return AnalysisReport(
        total_rows=total_rows,
        valid_rows=total_rows,
        totals_by_currency={"USD": Decimal("300.00")},
    )


def blocking_report() -> AnalysisReport:
    return AnalysisReport(
        total_rows=3,
        valid_rows=2,
        totals_by_currency={"USD": Decimal("200.00")},
        issues=(
            AnalysisIssue(
                row_number=2,
                code=IssueCode.INVALID_AMOUNT,
                severity=Severity.ERROR,
                message="bad amount",
            ),
        ),
    )


def make_batch(status: BatchStatus = BatchStatus.DRAFT, **overrides: Any) -> Batch:
    """Rehydrate a batch directly in `status` (as a repository would)."""
    values: dict[str, Any] = {
        "id": uuid4(),
        "reference": "test-batch",
        "created_by": SUBMITTER,
        "created_at": NOW,
        "rows": make_rows(),
        "status": status,
    }
    values.update(overrides)
    return Batch.model_validate(values)


def csv_bytes(*lines: str, header: str = CSV_HEADER) -> bytes:
    return "\n".join([header, *lines]).encode()


CLEAN_CSV = csv_bytes(
    "TX-1,ACC-1,100.00,USD,2026-09-01",
    "TX-2,ACC-2,150.50,USD,2026-09-02",
    "TX-3,ACC-3,99.99,EUR,2026-09-03",
)

DIRTY_CSV = csv_bytes(
    "TX-1,ACC-1,100.00,USD,2026-09-01",
    "TX-1,ACC-2,abc,USD,2026-09-02",
)


def make_tx(
    amount: str = "100.00",
    value_date: str = "2026-09-01",
    *,
    direction: str = "OUTFLOW",
    account: str = "ACC-1",
    currency: str = "EUR",
    external_id: str | None = None,
    batch_id: UUID | None = None,
    row_number: int = 1,
) -> Transaction:
    return Transaction(
        batch_id=batch_id or uuid4(),
        row_number=row_number,
        external_id=external_id or f"TX-{uuid4().hex[:8]}",
        account=account,
        amount=Decimal(amount),
        currency=currency,
        value_date=date.fromisoformat(value_date),
        direction=Direction(direction),
    )
