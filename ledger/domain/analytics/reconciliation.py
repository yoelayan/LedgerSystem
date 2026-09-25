"""Reconcile approved movements with the entries of an ERP's accounting ledger.

Every approved payment should appear in the books, and every bank movement in the books
should come from an approved batch. Matching is one-to-one and runs in two passes:

1. **By reference.** An entry whose reference equals a movement's `external_id` is the
   same operation. If amount, currency, direction or date (beyond the tolerance)
   differ, it is reported as a *discrepancy*, not silently paired or split.
2. **By amount and date.** Entries without a usable reference are paired with a
   movement of the same amount, currency and direction, dated within the tolerance.
   When several fit, the closest date wins.

Whatever is left over is reported on its own side: approved but not booked, or booked
without an approved batch behind it.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ledger.domain.value_objects import Direction, Transaction


class LedgerEntry(BaseModel):
    """One line of the ERP export, already typed."""

    model_config = ConfigDict(frozen=True)

    row_number: int = Field(ge=1)
    reference: str = ""
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    entry_date: date
    direction: Direction
    description: str = ""


class MatchKind(StrEnum):
    REFERENCE = "REFERENCE"
    AMOUNT_AND_DATE = "AMOUNT_AND_DATE"


class Difference(StrEnum):
    AMOUNT = "AMOUNT"
    CURRENCY = "CURRENCY"
    DIRECTION = "DIRECTION"
    DATE = "DATE"


class Match(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaction: Transaction
    entry: LedgerEntry
    kind: MatchKind

    @property
    def days_apart(self) -> int:
        return abs((self.entry.entry_date - self.transaction.value_date).days)


class Discrepancy(BaseModel):
    """Same reference on both sides, but the details do not agree."""

    model_config = ConfigDict(frozen=True)

    transaction: Transaction
    entry: LedgerEntry
    differences: tuple[Difference, ...]


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    matches: tuple[Match, ...] = ()
    discrepancies: tuple[Discrepancy, ...] = ()
    only_in_ledgersystem: tuple[Transaction, ...] = ()
    only_in_erp: tuple[LedgerEntry, ...] = ()

    @property
    def is_balanced(self) -> bool:
        return not (self.discrepancies or self.only_in_ledgersystem or self.only_in_erp)


def reconcile(
    transactions: Sequence[Transaction],
    entries: Sequence[LedgerEntry],
    *,
    date_tolerance_days: int = 3,
) -> ReconciliationResult:
    unmatched_tx = dict(enumerate(transactions))
    by_reference: dict[str, list[int]] = defaultdict(list)
    for index, t in unmatched_tx.items():
        by_reference[_normalise(t.external_id)].append(index)

    matches: list[Match] = []
    discrepancies: list[Discrepancy] = []
    pending: list[LedgerEntry] = []

    for entry in entries:
        candidates = [
            i for i in by_reference.get(_normalise(entry.reference), []) if i in unmatched_tx
        ]
        if not entry.reference.strip() or not candidates:
            pending.append(entry)
            continue
        index = min(candidates, key=lambda i: _distance(unmatched_tx[i], entry))
        transaction = unmatched_tx.pop(index)
        differences = _differences(transaction, entry, date_tolerance_days)
        if differences:
            discrepancies.append(
                Discrepancy(transaction=transaction, entry=entry, differences=differences)
            )
        else:
            matches.append(Match(transaction=transaction, entry=entry, kind=MatchKind.REFERENCE))

    # Indexed by what must agree exactly, so the second pass does not compare everything
    # with everything.
    by_value: dict[tuple[Decimal, str, Direction], list[int]] = defaultdict(list)
    for index, t in unmatched_tx.items():
        by_value[(t.amount, t.currency, t.direction)].append(index)

    only_in_erp: list[LedgerEntry] = []
    for entry in pending:
        candidates = [
            i
            for i in by_value.get((entry.amount, entry.currency, entry.direction), [])
            if i in unmatched_tx and _distance(unmatched_tx[i], entry) <= date_tolerance_days
        ]
        if not candidates:
            only_in_erp.append(entry)
            continue
        index = min(candidates, key=lambda i: (_distance(unmatched_tx[i], entry), i))
        matches.append(
            Match(transaction=unmatched_tx.pop(index), entry=entry, kind=MatchKind.AMOUNT_AND_DATE)
        )

    return ReconciliationResult(
        matches=tuple(matches),
        discrepancies=tuple(discrepancies),
        only_in_ledgersystem=tuple(unmatched_tx[i] for i in sorted(unmatched_tx)),
        only_in_erp=tuple(only_in_erp),
    )


def _differences(
    transaction: Transaction, entry: LedgerEntry, tolerance_days: int
) -> tuple[Difference, ...]:
    found = []
    if transaction.amount != entry.amount:
        found.append(Difference.AMOUNT)
    if transaction.currency != entry.currency:
        found.append(Difference.CURRENCY)
    if transaction.direction is not entry.direction:
        found.append(Difference.DIRECTION)
    if _distance(transaction, entry) > tolerance_days:
        found.append(Difference.DATE)
    return tuple(found)


def _distance(transaction: Transaction, entry: LedgerEntry) -> int:
    return abs((entry.entry_date - transaction.value_date).days)


def _normalise(reference: str) -> str:
    return reference.strip().upper()
