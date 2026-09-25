"""AnalyticsService with in-memory fakes: orchestration only, the engines have their own tests."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from ledger.application.analytics import AnalyticsService
from ledger.application.dtos import ReconcileCommand, TransactionQuery
from ledger.domain.analytics.reconciliation import LedgerEntry, LedgerProblem, ParsedLedger
from ledger.domain.analytics.timeline import BatchEvent, BatchEventKind
from ledger.domain.analytics.trends import Granularity
from ledger.domain.value_objects import BatchStatus, ColumnMapping, Direction, Transaction
from tests.factories import make_tx


class FakeReadModel:
    def __init__(
        self, transactions: list[Transaction], events: list[BatchEvent] | None = None
    ) -> None:
        self._transactions = transactions
        self._events = events or []
        self.queries: list[TransactionQuery] = []

    def transactions(self, query: TransactionQuery) -> list[Transaction]:
        self.queries.append(query)
        return [
            t
            for t in self._transactions
            if (query.date_from is None or t.value_date >= query.date_from)
            and (query.date_to is None or t.value_date <= query.date_to)
        ]

    def batch_events(self, query: TransactionQuery) -> list[BatchEvent]:
        return self._events

    def currencies(self) -> list[str]:
        return sorted({t.currency for t in self._transactions})


class FakeLedgerParser:
    def __init__(self, ledger: ParsedLedger) -> None:
        self.ledger = ledger
        self.calls: list[tuple[str, bool]] = []

    def parse(self, content: bytes, *, default_currency: str, invert: bool = False) -> ParsedLedger:
        self.calls.append((default_currency, invert))
        return self.ledger


def _entry(amount: str, day: str, reference: str = "", currency: str = "EUR") -> LedgerEntry:
    return LedgerEntry(
        row_number=1,
        reference=reference,
        amount=Decimal(amount),
        currency=currency,
        entry_date=date.fromisoformat(day),
        direction=Direction.OUTFLOW,
    )


def _service(
    transactions: list[Transaction],
    ledger: ParsedLedger | None = None,
    events: list[BatchEvent] | None = None,
) -> AnalyticsService:
    return AnalyticsService(
        read_model=FakeReadModel(transactions, events),
        ledger_parser=FakeLedgerParser(
            ledger or ParsedLedger(entries=(), column_mapping=ColumnMapping(), direction_rule="")
        ),
    )


def test_query_defaults_to_approved_batches() -> None:
    assert TransactionQuery().statuses == (BatchStatus.APPROVED,)
    assert TransactionQuery(include_pending=True).statuses == (
        BatchStatus.APPROVED,
        BatchStatus.PENDING_APPROVAL,
    )


def test_overview() -> None:
    service = _service(
        [make_tx("10.00", "2026-08-10"), make_tx("20.00", "2026-09-10", currency="USD")]
    )

    result = service.overview(TransactionQuery(), Granularity.MONTH)

    assert result.transaction_count == 2
    assert list(result.summary) == ["EUR", "USD"]
    assert {t.currency for t in result.trends} == {"EUR", "USD"}
    assert len(result.top_accounts) == 2
    assert service.currencies() == ["EUR", "USD"]


def test_timeline_merges_days_and_events() -> None:
    event = BatchEvent(
        batch_id=uuid4(),
        reference="sept",
        kind=BatchEventKind.APPROVED,
        at=datetime(2026, 9, 3, 12, tzinfo=UTC),
        actor="bob",
    )

    result = _service([make_tx("10.00", "2026-09-01")], events=[event]).timeline(TransactionQuery())

    assert [e.day for e in result.entries] == [date(2026, 9, 3), date(2026, 9, 1)]


def test_aml_uses_the_default_policy() -> None:
    movements = [
        make_tx(a, f"2026-09-0{i + 1}") for i, a in enumerate(["9500.00", "9800.00", "9100.00"])
    ]

    result = _service(movements).aml_alerts(TransactionQuery())

    assert result.transaction_count == 3
    assert [a.rule for a in result.alerts] == ["STRUCTURING"]


class TestReconcile:
    def test_compares_only_the_ledger_period_and_currencies(self) -> None:
        in_period = make_tx("100.00", "2026-09-10", external_id="OP-1")
        just_before = make_tx("55.00", "2026-09-08", external_id="OP-0")  # inside the tolerance
        long_before = make_tx("70.00", "2026-08-01", external_id="OP-X")
        other_currency = make_tx("100.00", "2026-09-10", currency="USD")
        ledger = ParsedLedger(
            entries=(_entry("100.00", "2026-09-10", "OP-1"), _entry("9.99", "2026-09-12")),
            problems=(LedgerProblem(row_number=3, message="x"),),
            column_mapping=ColumnMapping(),
            direction_rule="rule",
        )
        service = _service([in_period, just_before, long_before, other_currency], ledger)

        result = service.reconcile(
            ReconcileCommand(content=b"x", default_currency="USD", invert_debit_credit=True)
        )

        assert [m.transaction.external_id for m in result.result.matches] == ["OP-1"]
        # OP-0 was a candidate (tolerance) but belongs to the previous period: not reported.
        assert result.result.only_in_ledgersystem == ()
        assert [e.amount for e in result.result.only_in_erp] == [Decimal("9.99")]
        assert (result.period_from, result.period_to) == (date(2026, 9, 7), date(2026, 9, 15))
        assert result.problems[0].row_number == 3
        assert result.ledger_entries == 2

    def test_an_empty_ledger_compares_nothing(self) -> None:
        service = _service([make_tx()])

        result = service.reconcile(ReconcileCommand(content=b"x"))

        assert result.result.is_balanced
        assert result.period_from is None
