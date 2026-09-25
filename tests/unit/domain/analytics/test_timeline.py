from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from ledger.domain.analytics.timeline import (
    BatchEvent,
    BatchEventKind,
    chronology,
    daily_balances,
)
from tests.factories import make_tx


def test_daily_balances_accumulate_the_net_flow_per_currency() -> None:
    days = daily_balances(
        [
            make_tx("100.00", "2026-09-01", direction="INFLOW"),
            make_tx("30.00", "2026-09-01"),
            make_tx("50.00", "2026-09-03"),
            make_tx("7.00", "2026-09-02", currency="USD", direction="INFLOW"),
        ]
    )

    assert [(d.currency, d.day.day, d.totals.net, d.balance) for d in days] == [
        ("EUR", 1, Decimal("70.00"), Decimal("70.00")),
        ("EUR", 3, Decimal("-50.00"), Decimal("20.00")),
        ("USD", 2, Decimal("7.00"), Decimal("7.00")),
    ]


def test_chronology_merges_movements_and_events_newest_first() -> None:
    days = daily_balances(
        [make_tx("10.00", "2026-09-01"), make_tx("5.00", "2026-09-01", currency="USD")]
    )
    uploaded = BatchEvent(
        batch_id=uuid4(),
        reference="sept",
        kind=BatchEventKind.UPLOADED,
        at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        actor="alice",
    )
    approved = uploaded.model_copy(
        update={
            "kind": BatchEventKind.APPROVED,
            "at": datetime(2026, 9, 2, 17, tzinfo=UTC),
            "actor": "bob",
        }
    )

    entries = chronology(days, [approved, uploaded])

    assert [e.day for e in entries] == [date(2026, 9, 2), date(2026, 9, 1)]
    assert [e.actor for e in entries[0].events] == ["alice", "bob"]
    assert [m.currency for m in entries[1].movements] == ["EUR", "USD"]
    assert entries[0].movements == ()
