from datetime import date
from decimal import Decimal

import pytest

from ledger.domain.analytics.trends import (
    Granularity,
    Totals,
    next_period,
    period_start,
    period_totals,
    summarize,
    top_accounts,
    trends,
)
from tests.factories import make_tx


@pytest.mark.parametrize(
    ("granularity", "start", "following"),
    [
        (Granularity.DAY, date(2026, 9, 17), date(2026, 9, 18)),
        (Granularity.WEEK, date(2026, 9, 14), date(2026, 9, 21)),  # Thursday -> its Monday
        (Granularity.MONTH, date(2026, 9, 1), date(2026, 10, 1)),
    ],
)
def test_periods(granularity: Granularity, start: date, following: date) -> None:
    assert period_start(date(2026, 9, 17), granularity) == start
    assert next_period(start, granularity) == following


def test_next_month_across_the_year() -> None:
    assert next_period(date(2026, 12, 1), Granularity.MONTH) == date(2027, 1, 1)


def test_summarize_splits_inflows_and_outflows() -> None:
    result = summarize(
        [
            make_tx("100.00", direction="INFLOW"),
            make_tx("30.00"),
            make_tx("5.00", currency="USD"),
        ]
    )

    assert list(result) == ["EUR", "USD"]
    assert result["EUR"] == Totals(inflow=Decimal("100.00"), outflow=Decimal("30.00"), count=2)
    assert result["EUR"].net == Decimal("70.00")


def test_period_totals_fill_the_gaps() -> None:
    series = period_totals(
        [make_tx("10.00", "2026-07-15"), make_tx("20.00", "2026-09-02")], Granularity.MONTH
    )

    assert [(p.period.month, p.totals.outflow) for p in series] == [
        (7, Decimal("10.00")),
        (8, Decimal("0.00")),
        (9, Decimal("20.00")),
    ]


def test_period_totals_of_nothing() -> None:
    assert period_totals([], Granularity.DAY) == []


def test_trend_compares_with_previous_and_baseline() -> None:
    movements = [
        make_tx("100.00", "2026-06-10"),
        make_tx("100.00", "2026-07-10"),
        make_tx("200.00", "2026-08-10"),
        make_tx("300.00", "2026-09-10"),
        make_tx("50.00", "2026-09-11", direction="INFLOW"),
    ]

    [trend] = trends(period_totals(movements, Granularity.MONTH))

    assert trend.period == date(2026, 9, 1)
    assert trend.outflow_change == Decimal("50.0")  # 300 vs 200
    assert trend.inflow_change is None  # nothing came in the month before
    assert trend.baseline_outflow == Decimal("133.33")  # (100 + 100 + 200) / 3


def test_trend_with_a_single_period() -> None:
    [trend] = trends(period_totals([make_tx()], Granularity.MONTH))

    assert trend.previous is None
    assert trend.outflow_change is None
    assert trend.baseline_outflow is None


def test_top_accounts_by_volume() -> None:
    movements = [
        make_tx("10.00", account="SMALL"),
        make_tx("500.00", account="BIG"),
        make_tx("400.00", account="BIG", direction="INFLOW"),
        make_tx("50.00", account="MID"),
    ]

    top = top_accounts(movements, limit=2)

    assert [(a.account, a.volume) for a in top] == [
        ("BIG", Decimal("900.00")),
        ("MID", Decimal("50.00")),
    ]
