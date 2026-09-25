"""Volumes over time: per-period totals, period-over-period trends and busiest accounts."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ledger.domain.value_objects import Direction, Transaction

ZERO: Final = Decimal("0.00")
# Periods averaged to judge whether the latest one is unusual.
BASELINE_PERIODS: Final = 3


class Granularity(StrEnum):
    DAY = "DAY"
    WEEK = "WEEK"  # ISO weeks, starting on Monday
    MONTH = "MONTH"


def period_start(day: date, granularity: Granularity) -> date:
    if granularity is Granularity.DAY:
        return day
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def next_period(start: date, granularity: Granularity) -> date:
    if granularity is Granularity.DAY:
        return start + timedelta(days=1)
    if granularity is Granularity.WEEK:
        return start + timedelta(weeks=1)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


class Totals(BaseModel):
    model_config = ConfigDict(frozen=True)

    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    count: int = Field(default=0, ge=0)

    @property
    def net(self) -> Decimal:
        return self.inflow - self.outflow

    def add(self, transaction: Transaction) -> "Totals":
        inflow = transaction.direction is Direction.INFLOW
        return Totals(
            inflow=self.inflow + (transaction.amount if inflow else ZERO),
            outflow=self.outflow + (ZERO if inflow else transaction.amount),
            count=self.count + 1,
        )


class PeriodTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: date
    currency: str
    totals: Totals


class Trend(BaseModel):
    """How the latest period compares with the one before and with the recent average."""

    model_config = ConfigDict(frozen=True)

    currency: str
    period: date
    current: Totals
    previous: Totals | None
    baseline_inflow: Decimal | None  # average of up to BASELINE_PERIODS earlier periods
    baseline_outflow: Decimal | None

    @property
    def inflow_change(self) -> Decimal | None:
        return _change(self.current.inflow, self.previous.inflow if self.previous else None)

    @property
    def outflow_change(self) -> Decimal | None:
        return _change(self.current.outflow, self.previous.outflow if self.previous else None)


class AccountActivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    account: str
    currency: str
    totals: Totals

    @property
    def volume(self) -> Decimal:
        return self.totals.inflow + self.totals.outflow


def summarize(transactions: Iterable[Transaction]) -> dict[str, Totals]:
    """Totals per currency."""
    result: dict[str, Totals] = defaultdict(Totals)
    for t in transactions:
        result[t.currency] = result[t.currency].add(t)
    return dict(sorted(result.items()))


def period_totals(
    transactions: Sequence[Transaction], granularity: Granularity
) -> list[PeriodTotals]:
    """Totals per currency and period, including empty periods so series have no gaps."""
    buckets: dict[tuple[str, date], Totals] = defaultdict(Totals)
    for t in transactions:
        key = (t.currency, period_start(t.value_date, granularity))
        buckets[key] = buckets[key].add(t)
    if not buckets:
        return []
    first = min(period for _, period in buckets)
    last = max(period for _, period in buckets)
    series = []
    for currency in sorted({currency for currency, _ in buckets}):
        period = first
        while period <= last:
            series.append(
                PeriodTotals(
                    period=period,
                    currency=currency,
                    totals=buckets.get((currency, period), Totals()),
                )
            )
            period = next_period(period, granularity)
    return series


def trends(series: Sequence[PeriodTotals]) -> list[Trend]:
    """One trend per currency, for its latest period."""
    by_currency: dict[str, list[PeriodTotals]] = defaultdict(list)
    for point in series:
        by_currency[point.currency].append(point)
    result = []
    for currency, points in sorted(by_currency.items()):
        points.sort(key=lambda p: p.period)
        *earlier, latest = points
        baseline = earlier[-BASELINE_PERIODS:]
        result.append(
            Trend(
                currency=currency,
                period=latest.period,
                current=latest.totals,
                previous=earlier[-1].totals if earlier else None,
                baseline_inflow=_average([p.totals.inflow for p in baseline]),
                baseline_outflow=_average([p.totals.outflow for p in baseline]),
            )
        )
    return result


def top_accounts(transactions: Iterable[Transaction], limit: int = 10) -> list[AccountActivity]:
    """Accounts with the most money moved (in + out), per currency."""
    buckets: dict[tuple[str, str], Totals] = defaultdict(Totals)
    for t in transactions:
        buckets[(t.account, t.currency)] = buckets[(t.account, t.currency)].add(t)
    activity = [
        AccountActivity(account=account, currency=currency, totals=totals)
        for (account, currency), totals in buckets.items()
    ]
    activity.sort(key=lambda a: (-a.volume, a.currency, a.account))
    return activity[:limit]


def _average(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, ZERO) / len(values)).quantize(Decimal("0.01"))


def _change(current: Decimal, previous: Decimal | None) -> Decimal | None:
    """Percentage change; None when there is nothing to compare against."""
    if previous is None or previous == 0:
        return None
    return ((current - previous) / previous * 100).quantize(Decimal("0.1"))
