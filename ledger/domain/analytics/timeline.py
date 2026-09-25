"""What happened, day by day: money moved (by value date) and what people did with batches."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ledger.domain.analytics.trends import ZERO, Totals
from ledger.domain.value_objects import Transaction


class BatchEventKind(StrEnum):
    UPLOADED = "UPLOADED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BatchEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: UUID
    reference: str
    kind: BatchEventKind
    at: AwareDatetime
    actor: str
    detail: str = ""


class TimelineDay(BaseModel):
    """Movements with one value date and currency, plus the running balance after them.

    The balance is the cumulative net flow (inflows minus outflows) since the first day
    shown, not an account balance: the system does not know opening balances.
    """

    model_config = ConfigDict(frozen=True)

    day: date
    currency: str
    totals: Totals
    balance: Decimal


class TimelineEntry(BaseModel):
    """One date of the chronology: its movements and the batch events on that date."""

    model_config = ConfigDict(frozen=True)

    day: date
    movements: tuple[TimelineDay, ...] = ()
    events: tuple[BatchEvent, ...] = ()


def daily_balances(transactions: Iterable[Transaction]) -> list[TimelineDay]:
    per_day: dict[tuple[str, date], Totals] = defaultdict(Totals)
    for t in transactions:
        per_day[(t.currency, t.value_date)] = per_day[(t.currency, t.value_date)].add(t)
    result = []
    balances: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for currency, day in sorted(per_day):
        totals = per_day[(currency, day)]
        balances[currency] += totals.net
        result.append(
            TimelineDay(day=day, currency=currency, totals=totals, balance=balances[currency])
        )
    return result


def chronology(days: Sequence[TimelineDay], events: Sequence[BatchEvent]) -> list[TimelineEntry]:
    """Movements and events merged by date, most recent first."""
    movements: dict[date, list[TimelineDay]] = defaultdict(list)
    for d in days:
        movements[d.day].append(d)
    happenings: dict[date, list[BatchEvent]] = defaultdict(list)
    for e in events:
        happenings[e.at.date()].append(e)
    return [
        TimelineEntry(
            day=day,
            movements=tuple(sorted(movements[day], key=lambda d: d.currency)),
            events=tuple(sorted(happenings[day], key=lambda e: e.at)),
        )
        for day in sorted(movements.keys() | happenings.keys(), reverse=True)
    ]
