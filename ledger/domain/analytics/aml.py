"""Money-laundering red flags over approved movements.

Each rule is a documented typology with explicit thresholds, so every alert can be
explained and challenged: which rule fired, on which movements, and why. These are
*indicators* for a person to review, not determinations: a legitimate business can
trigger any of them.

Rules (per account and currency):

* STRUCTURING: several amounts just below the reporting threshold within a few days
  ("pitufeo"): splitting a large amount to stay under the radar.
* PASS_THROUGH: money comes in and almost all of it leaves again within days: the
  account works as a transit account.
* ROUND_AMOUNTS: most movements are large round figures, uncommon in real trade.
* HIGH_VELOCITY: an unusual number of movements on a single day.
* SPIKE: a month's volume far above the account's own history (e.g. dormant accounts).
* REPEATED_PAYMENT: the same outflow (amount, account) in different batches within a
  few days: a possible duplicate payment, or a way to extract funds twice.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from statistics import median
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ledger.domain.value_objects import Direction, Transaction

# Rough equivalents of 10,000 USD, the most common cash-reporting threshold.
DEFAULT_REPORTING_THRESHOLDS: Final[Mapping[str, Decimal]] = {
    "USD": Decimal("10000"),
    "EUR": Decimal("10000"),
    "GBP": Decimal("8000"),
    "MXN": Decimal("180000"),
    "COP": Decimal("40000000"),
}


class AmlRule(StrEnum):
    STRUCTURING = "STRUCTURING"
    PASS_THROUGH = "PASS_THROUGH"
    ROUND_AMOUNTS = "ROUND_AMOUNTS"
    HIGH_VELOCITY = "HIGH_VELOCITY"
    SPIKE = "SPIKE"
    REPEATED_PAYMENT = "REPEATED_PAYMENT"


class AlertSeverity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AmlPolicy(BaseModel):
    """Thresholds of every rule. Tune them to the organisation's risk appetite."""

    model_config = ConfigDict(frozen=True)

    reporting_thresholds: dict[str, Decimal] = Field(
        default_factory=lambda: dict(DEFAULT_REPORTING_THRESHOLDS)
    )
    # STRUCTURING: at least `structuring_min_count` amounts in [ratio x threshold, threshold)
    structuring_ratio: Decimal = Decimal("0.80")
    structuring_window_days: int = Field(default=7, ge=1)
    structuring_min_count: int = Field(default=3, ge=2)
    # PASS_THROUGH: outflows >= ratio of an inflow (>= min share of the threshold) within the window
    pass_through_window_days: int = Field(default=3, ge=1)
    pass_through_ratio: Decimal = Decimal("0.90")
    pass_through_min_share: Decimal = Decimal("0.50")
    # ROUND_AMOUNTS: multiples of threshold / 10, in at least this share of >= min_count movements
    round_min_count: int = Field(default=3, ge=2)
    round_min_share: Decimal = Decimal("0.60")
    # HIGH_VELOCITY: movements of one account on one day
    velocity_max_per_day: int = Field(default=10, ge=2)
    # SPIKE: a month above factor x the median of at least `spike_history_months` earlier months
    spike_factor: Decimal = Decimal("5")
    spike_history_months: int = Field(default=3, ge=1)
    # REPEATED_PAYMENT: same outflow in another batch within the window
    repeated_window_days: int = Field(default=7, ge=0)

    def threshold(self, currency: str) -> Decimal | None:
        return self.reporting_thresholds.get(currency)


class AmlAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: AmlRule
    severity: AlertSeverity
    account: str
    currency: str
    transactions: tuple[Transaction, ...]
    total: Decimal
    first_date: date
    last_date: date
    # Rule-specific figures that explain the alert (threshold, ratio, count...).
    facts: dict[str, str] = Field(default_factory=dict)


def scan(transactions: Iterable[Transaction], policy: AmlPolicy | None = None) -> list[AmlAlert]:
    policy = policy or AmlPolicy()
    by_account: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for t in transactions:
        by_account[(t.account, t.currency)].append(t)
    alerts: list[AmlAlert] = []
    for (account, currency), movements in sorted(by_account.items()):
        movements.sort(key=lambda t: (t.value_date, str(t.batch_id), t.row_number))
        alerts += _structuring(account, currency, movements, policy)
        alerts += _pass_through(account, currency, movements, policy)
        alerts += _round_amounts(account, currency, movements, policy)
        alerts += _velocity(account, currency, movements, policy)
        alerts += _spike(account, currency, movements, policy)
        alerts += _repeated_payments(account, currency, movements, policy)
    severity_rank = {AlertSeverity.HIGH: 0, AlertSeverity.MEDIUM: 1, AlertSeverity.LOW: 2}
    alerts.sort(key=lambda a: (severity_rank[a.severity], -a.total, a.account, a.rule))
    return alerts


def _alert(
    rule: AmlRule,
    severity: AlertSeverity,
    account: str,
    currency: str,
    movements: Sequence[Transaction],
    **facts: object,
) -> AmlAlert:
    return AmlAlert(
        rule=rule,
        severity=severity,
        account=account,
        currency=currency,
        transactions=tuple(movements),
        total=sum((t.amount for t in movements), Decimal("0.00")),
        first_date=min(t.value_date for t in movements),
        last_date=max(t.value_date for t in movements),
        facts={key: str(value) for key, value in facts.items()},
    )


def _windows(movements: Sequence[Transaction], days: int) -> Iterable[tuple[int, int]]:
    """(start, end) index pairs of the longest run starting at each movement within `days`."""
    end = 0
    for start, first in enumerate(movements):
        end = max(end, start)
        while end + 1 < len(movements) and (
            movements[end + 1].value_date - first.value_date
        ) < timedelta(days=days):
            end += 1
        yield start, end


def _structuring(
    account: str, currency: str, movements: Sequence[Transaction], policy: AmlPolicy
) -> list[AmlAlert]:
    threshold = policy.threshold(currency)
    if threshold is None:
        return []
    floor = threshold * policy.structuring_ratio
    near = [t for t in movements if floor <= t.amount < threshold]
    alerts = []
    covered: set[int] = set()
    for start, end in _windows(near, policy.structuring_window_days):
        group = near[start : end + 1]
        if start in covered or len(group) < policy.structuring_min_count:
            continue
        covered.update(range(start, end + 1))
        total = sum((t.amount for t in group), Decimal("0"))
        alerts.append(
            _alert(
                AmlRule.STRUCTURING,
                AlertSeverity.HIGH if total >= threshold else AlertSeverity.MEDIUM,
                account,
                currency,
                group,
                threshold=threshold,
                window_days=policy.structuring_window_days,
                count=len(group),
            )
        )
    return alerts


def _pass_through(
    account: str, currency: str, movements: Sequence[Transaction], policy: AmlPolicy
) -> list[AmlAlert]:
    threshold = policy.threshold(currency)
    if threshold is None:
        return []
    alerts = []
    used: set[tuple[str, int]] = set()
    window = timedelta(days=policy.pass_through_window_days)
    for inflow in movements:
        if (
            inflow.direction is not Direction.INFLOW
            or inflow.amount < threshold * policy.pass_through_min_share
            or _key(inflow) in used
        ):
            continue
        outflows = [
            t
            for t in movements
            if t.direction is Direction.OUTFLOW
            and _key(t) not in used
            and timedelta(0) <= t.value_date - inflow.value_date <= window
        ]
        out_total = sum((t.amount for t in outflows), Decimal("0"))
        ratio = out_total / inflow.amount
        if ratio < policy.pass_through_ratio:
            continue
        used.update(_key(t) for t in (inflow, *outflows))
        alerts.append(
            _alert(
                AmlRule.PASS_THROUGH,
                AlertSeverity.HIGH,
                account,
                currency,
                [inflow, *outflows],
                inflow=inflow.amount,
                outflow=out_total,
                ratio_pct=(ratio * 100).quantize(Decimal("1")),
                window_days=policy.pass_through_window_days,
            )
        )
    return alerts


def _round_amounts(
    account: str, currency: str, movements: Sequence[Transaction], policy: AmlPolicy
) -> list[AmlAlert]:
    threshold = policy.threshold(currency)
    if threshold is None or len(movements) < policy.round_min_count:
        return []
    unit = threshold / 10
    rounded = [t for t in movements if t.amount % unit == 0]
    share = Decimal(len(rounded)) / len(movements)
    if len(rounded) < policy.round_min_count or share < policy.round_min_share:
        return []
    return [
        _alert(
            AmlRule.ROUND_AMOUNTS,
            AlertSeverity.LOW,
            account,
            currency,
            rounded,
            unit=unit.normalize(),
            share_pct=(share * 100).quantize(Decimal("1")),
            count=len(rounded),
            of=len(movements),
        )
    ]


def _velocity(
    account: str, currency: str, movements: Sequence[Transaction], policy: AmlPolicy
) -> list[AmlAlert]:
    per_day: dict[date, list[Transaction]] = defaultdict(list)
    for t in movements:
        per_day[t.value_date].append(t)
    return [
        _alert(
            AmlRule.HIGH_VELOCITY,
            AlertSeverity.MEDIUM,
            account,
            currency,
            day_movements,
            count=len(day_movements),
            limit=policy.velocity_max_per_day,
        )
        for day, day_movements in sorted(per_day.items())
        if len(day_movements) > policy.velocity_max_per_day
    ]


def _spike(
    account: str, currency: str, movements: Sequence[Transaction], policy: AmlPolicy
) -> list[AmlAlert]:
    months: dict[date, list[Transaction]] = defaultdict(list)
    for t in movements:
        months[t.value_date.replace(day=1)].append(t)
    ordered = sorted(months)
    alerts = []
    for index, month in enumerate(ordered):
        history = [_volume(months[m]) for m in ordered[:index]]
        if len(history) < policy.spike_history_months:
            continue
        usual = Decimal(median(history))
        volume = _volume(months[month])
        if usual > 0 and volume > usual * policy.spike_factor:
            alerts.append(
                _alert(
                    AmlRule.SPIKE,
                    AlertSeverity.MEDIUM,
                    account,
                    currency,
                    months[month],
                    month=month.strftime("%Y-%m"),
                    usual=usual.quantize(Decimal("0.01")),
                    factor=(volume / usual).quantize(Decimal("0.1")),
                )
            )
    return alerts


def _repeated_payments(
    account: str, currency: str, movements: Sequence[Transaction], policy: AmlPolicy
) -> list[AmlAlert]:
    by_amount: dict[Decimal, list[Transaction]] = defaultdict(list)
    for t in movements:
        if t.direction is Direction.OUTFLOW:
            by_amount[t.amount].append(t)
    alerts = []
    window = timedelta(days=policy.repeated_window_days)
    for amount, same in sorted(by_amount.items()):
        for first, second in pairwise(same):
            if first.batch_id != second.batch_id and second.value_date - first.value_date <= window:
                alerts.append(
                    _alert(
                        AmlRule.REPEATED_PAYMENT,
                        AlertSeverity.MEDIUM,
                        account,
                        currency,
                        [first, second],
                        amount=amount,
                        days_apart=(second.value_date - first.value_date).days,
                    )
                )
    return alerts


def _volume(movements: Iterable[Transaction]) -> Decimal:
    return sum((t.amount for t in movements), Decimal("0"))


def _key(t: Transaction) -> tuple[str, int]:
    return (str(t.batch_id), t.row_number)
