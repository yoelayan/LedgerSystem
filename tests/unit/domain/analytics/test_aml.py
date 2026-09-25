from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from ledger.domain.analytics.aml import AlertSeverity, AmlPolicy, AmlRule, scan
from ledger.domain.value_objects import Transaction
from tests.factories import make_tx


def _rules(transactions: list[Transaction], policy: AmlPolicy | None = None) -> list[AmlRule]:
    return [a.rule for a in scan(transactions, policy)]


def _day(offset: int) -> str:
    return (date(2026, 9, 1) + timedelta(days=offset)).isoformat()


def test_ordinary_activity_raises_nothing() -> None:
    movements = [make_tx(f"{100 + i}.37", _day(i)) for i in range(10)]

    assert scan(movements) == []


class TestStructuring:
    def test_amounts_just_below_the_threshold_within_days(self) -> None:
        movements = [
            make_tx(amount, _day(i)) for i, amount in enumerate(["9500.00", "9800.00", "9100.00"])
        ]

        [alert] = scan(movements)

        assert alert.rule is AmlRule.STRUCTURING
        assert alert.severity is AlertSeverity.HIGH  # together they exceed 10,000
        assert alert.total == Decimal("28400.00")
        assert alert.facts == {"threshold": "10000", "window_days": "7", "count": "3"}

    def test_spread_over_too_long_a_period_is_not_flagged(self) -> None:
        movements = [make_tx("9500.00", _day(i * 10)) for i in range(3)]

        assert AmlRule.STRUCTURING not in _rules(movements)

    def test_severity_is_medium_below_the_threshold_in_total(self) -> None:
        policy = AmlPolicy(structuring_ratio=Decimal("0.01"), structuring_min_count=2)
        movements = [make_tx("200.00", _day(0)), make_tx("300.00", _day(1))]

        [alert] = scan(movements, policy)

        assert alert.severity is AlertSeverity.MEDIUM

    def test_overlapping_windows_raise_a_single_alert(self) -> None:
        movements = [make_tx("9500.00", _day(i)) for i in range(4)]

        assert _rules(movements).count(AmlRule.STRUCTURING) == 1


def test_pass_through_money_in_and_out_again() -> None:
    movements = [
        make_tx("20000.00", _day(0), direction="INFLOW"),
        make_tx("12000.00", _day(1)),
        make_tx("7000.00", _day(2)),
        make_tx("500.00", _day(9)),  # outside the window
    ]

    [alert] = [a for a in scan(movements) if a.rule is AmlRule.PASS_THROUGH]

    assert len(alert.transactions) == 3
    assert alert.facts["ratio_pct"] == "95"


def test_pass_through_needs_most_of_the_money_to_leave() -> None:
    movements = [make_tx("20000.00", _day(0), direction="INFLOW"), make_tx("5000.00", _day(1))]

    assert AmlRule.PASS_THROUGH not in _rules(movements)


def test_round_amounts() -> None:
    movements = [
        make_tx(a, _day(i)) for i, a in enumerate(["5000.00", "3000.00", "8000.00", "123.45"])
    ]

    [alert] = scan(movements)

    assert alert.rule is AmlRule.ROUND_AMOUNTS
    assert alert.facts == {"unit": "1000", "share_pct": "75", "count": "3", "of": "4"}


def test_high_velocity() -> None:
    movements = [make_tx(f"{10 + i}.01", _day(0)) for i in range(11)]

    [alert] = scan(movements)

    assert alert.rule is AmlRule.HIGH_VELOCITY
    assert alert.facts["count"] == "11"


def test_spike_against_the_account_history() -> None:
    usual = [make_tx("100.01", f"2026-0{m}-10") for m in (5, 6, 7, 8)]
    spike = make_tx("900.01", "2026-09-10")

    [alert] = scan([*usual, spike])

    assert alert.rule is AmlRule.SPIKE
    assert alert.facts["month"] == "2026-09"


def test_spike_needs_enough_history() -> None:
    assert AmlRule.SPIKE not in _rules(
        [make_tx("100.01", "2026-08-10"), make_tx("9000.01", "2026-09-10")]
    )


def test_repeated_payment_across_batches() -> None:
    first = make_tx("730.55", _day(0), batch_id=uuid4())
    again = make_tx("730.55", _day(2), batch_id=uuid4())
    same_batch = make_tx("730.55", _day(3), batch_id=again.batch_id, row_number=2)

    alerts = [a for a in scan([first, again, same_batch]) if a.rule is AmlRule.REPEATED_PAYMENT]

    assert [len(a.transactions) for a in alerts] == [2]
    assert alerts[0].facts == {"amount": "730.55", "days_apart": "2"}


def test_currencies_without_a_threshold_skip_threshold_rules() -> None:
    movements = [make_tx(f"950{i}.00", _day(i), currency="JPY") for i in range(3)]

    assert _rules(movements) == []


def test_alerts_are_sorted_by_severity() -> None:
    structuring = [
        make_tx(a, _day(i), account="A") for i, a in enumerate(["9500.00", "9800.00", "9100.00"])
    ]
    velocity = [make_tx(f"{10 + i}.01", _day(0), account="B") for i in range(11)]

    alerts = scan([*velocity, *structuring])

    assert [a.severity for a in alerts] == [AlertSeverity.HIGH, AlertSeverity.MEDIUM]
