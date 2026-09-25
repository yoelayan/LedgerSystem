from datetime import date
from decimal import Decimal

from ledger.domain.analytics.reconciliation import (
    Difference,
    LedgerEntry,
    MatchKind,
    reconcile,
)
from ledger.domain.value_objects import Direction
from tests.factories import make_tx


def _entry(
    amount: str = "100.00",
    day: str = "2026-09-01",
    reference: str = "",
    *,
    currency: str = "EUR",
    direction: Direction = Direction.OUTFLOW,
    row: int = 1,
) -> LedgerEntry:
    return LedgerEntry(
        row_number=row,
        reference=reference,
        amount=Decimal(amount),
        currency=currency,
        entry_date=date.fromisoformat(day),
        direction=direction,
    )


def test_matching_by_reference_tolerates_a_booking_delay() -> None:
    tx = make_tx("100.00", "2026-09-01", external_id="OP-1")

    result = reconcile([tx], [_entry("100.00", "2026-09-03", " op-1 ")])

    [match] = result.matches
    assert (match.kind, match.days_apart) == (MatchKind.REFERENCE, 2)
    assert result.is_balanced


def test_same_reference_with_different_details_is_a_discrepancy() -> None:
    tx = make_tx("100.00", "2026-09-01", external_id="OP-1")
    entry = _entry("110.00", "2026-09-20", "OP-1", currency="USD", direction=Direction.INFLOW)

    result = reconcile([tx], [entry])

    [discrepancy] = result.discrepancies
    assert discrepancy.differences == (
        Difference.AMOUNT,
        Difference.CURRENCY,
        Difference.DIRECTION,
        Difference.DATE,
    )
    assert not result.matches
    assert not result.is_balanced


def test_without_reference_amount_and_closest_date_decide() -> None:
    far = make_tx("100.00", "2026-09-01", external_id="A")
    near = make_tx("100.00", "2026-09-04", external_id="B")

    result = reconcile([far, near], [_entry("100.00", "2026-09-05")])

    [match] = result.matches
    assert match.kind is MatchKind.AMOUNT_AND_DATE
    assert match.transaction.external_id == "B"
    assert [t.external_id for t in result.only_in_ledgersystem] == ["A"]


def test_an_unknown_reference_falls_back_to_amount_and_date() -> None:
    tx = make_tx("100.00", "2026-09-01", external_id="OP-1")

    result = reconcile([tx], [_entry("100.00", "2026-09-01", "ASIENTO-99")])

    assert result.matches[0].kind is MatchKind.AMOUNT_AND_DATE


def test_leftovers_on_both_sides() -> None:
    tx = make_tx("100.00", "2026-09-01", external_id="OP-1")
    too_late = _entry("100.00", "2026-09-10", row=1)
    other_amount = _entry("99.99", "2026-09-01", row=2)

    result = reconcile([tx], [too_late, other_amount], date_tolerance_days=3)

    assert result.only_in_ledgersystem == (tx,)
    assert [e.row_number for e in result.only_in_erp] == [1, 2]


def test_each_movement_is_matched_once() -> None:
    tx = make_tx("100.00", "2026-09-01", external_id="OP-1")

    result = reconcile([tx], [_entry(reference="OP-1", row=1), _entry(reference="OP-1", row=2)])

    assert len(result.matches) == 1
    assert [e.row_number for e in result.only_in_erp] == [2]
