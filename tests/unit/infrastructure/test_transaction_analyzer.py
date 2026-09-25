from decimal import Decimal

import pytest

from ledger.domain.value_objects import IssueCode, RawTransactionRow, Severity
from ledger.infrastructure.analysis.transaction_analyzer import PandasTransactionAnalyzer
from tests.factories import make_row

analyzer = PandasTransactionAnalyzer()


def _codes(rows: list[RawTransactionRow]) -> list[tuple[int, IssueCode]]:
    return [(i.row_number, i.code) for i in analyzer.analyze(rows).issues]


def test_clean_rows_produce_no_issues_and_decimal_totals() -> None:
    rows = [
        make_row(1, amount="100.10"),
        make_row(2, amount="0.20"),
        make_row(3, amount="50", currency="eur"),
    ]

    report = analyzer.analyze(rows)

    assert report.issues == ()
    assert report.total_rows == 3
    assert report.valid_rows == 3
    # 100.10 + 0.20 is exactly 100.30 with Decimal (it is not with floats).
    assert report.totals_by_currency == {"USD": Decimal("100.30"), "EUR": Decimal("50.00")}


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"account": ""}, IssueCode.MISSING_VALUE),
        ({"amount": "12,50"}, IssueCode.INVALID_AMOUNT),
        ({"amount": "abc"}, IssueCode.INVALID_AMOUNT),
        ({"amount": "NaN"}, IssueCode.INVALID_AMOUNT),
        ({"amount": "Infinity"}, IssueCode.INVALID_AMOUNT),
        ({"amount": "0"}, IssueCode.NON_POSITIVE_AMOUNT),
        ({"amount": "-10.00"}, IssueCode.NON_POSITIVE_AMOUNT),
        ({"amount": "10.001"}, IssueCode.EXCESSIVE_PRECISION),
        ({"currency": "XYZ"}, IssueCode.UNSUPPORTED_CURRENCY),
        ({"value_date": "2026-13-40"}, IssueCode.INVALID_VALUE_DATE),
        ({"value_date": "01/09/2026"}, IssueCode.INVALID_VALUE_DATE),
    ],
)
def test_each_blocking_rule(overrides: dict[str, str], expected_code: IssueCode) -> None:
    rows = [make_row(1), make_row(2, **overrides)]

    report = analyzer.analyze(rows)

    assert [(i.row_number, i.code) for i in report.issues] == [(2, expected_code)]
    assert report.issues[0].severity is Severity.ERROR
    assert report.has_blocking_issues
    assert report.valid_rows == 1
    assert report.totals_by_currency == {"USD": Decimal("100.00")}


def test_empty_amount_is_only_reported_as_missing() -> None:
    assert _codes([make_row(1, amount="")]) == [(1, IssueCode.MISSING_VALUE)]


def test_duplicates_flag_every_repetition_but_the_first() -> None:
    rows = [
        make_row(1, external_id="A"),
        make_row(2, external_id="A"),
        make_row(3, external_id="A"),
    ]

    assert _codes(rows) == [
        (2, IssueCode.DUPLICATE_EXTERNAL_ID),
        (3, IssueCode.DUPLICATE_EXTERNAL_ID),
    ]


def test_a_row_can_break_several_rules() -> None:
    row = make_row(1, amount="-1.005", currency="XYZ")

    assert _codes([row]) == [
        (1, IssueCode.EXCESSIVE_PRECISION),
        (1, IssueCode.NON_POSITIVE_AMOUNT),
        (1, IssueCode.UNSUPPORTED_CURRENCY),
    ]


def test_outlier_is_a_warning_and_does_not_block() -> None:
    amounts = ["100.00", "101.00", "99.00", "102.00", "98.00", "100.00", "5000.00"]
    rows = [make_row(i, amount=a) for i, a in enumerate(amounts, start=1)]

    report = analyzer.analyze(rows)

    assert [(i.row_number, i.code, i.severity) for i in report.issues] == [
        (7, IssueCode.AMOUNT_OUTLIER, Severity.WARNING)
    ]
    assert not report.has_blocking_issues
    assert report.valid_rows == 7
    assert report.totals_by_currency == {"USD": Decimal("5600.00")}


def test_outliers_are_computed_per_currency() -> None:
    usd = [make_row(i, amount="100.00") for i in range(1, 6)]
    eur = [
        make_row(i, amount=a, currency="EUR")
        for i, a in enumerate(["9000", "9100", "8900", "9050", "8950"], start=6)
    ]

    assert analyzer.analyze(usd + eur).issues == ()


def test_small_samples_are_not_scored() -> None:
    rows = [make_row(1, amount="1.00"), make_row(2, amount="2.00"), make_row(3, amount="99999.00")]

    assert analyzer.analyze(rows).issues == ()


def test_invalid_rows_do_not_distort_outlier_statistics() -> None:
    amounts = ["100.00", "101.00", "99.00", "102.00", "98.00"]
    rows = [make_row(i, amount=a) for i, a in enumerate(amounts, start=1)]
    rows.append(make_row(6, amount="-999999.00"))

    assert _codes(rows) == [(6, IssueCode.NON_POSITIVE_AMOUNT)]


def test_unrecognised_direction_blocks() -> None:
    assert _codes([make_row(1, direction="Nómina")]) == [(1, IssueCode.INVALID_DIRECTION)]


def test_totals_are_split_into_inflows_and_outflows() -> None:
    report = analyzer.analyze(
        [
            make_row(1, amount="100.00", direction="INFLOW"),
            make_row(2, amount="30.00", direction="OUTFLOW"),
            make_row(3, amount="20.00", direction="OUTFLOW", currency="EUR"),
        ]
    )

    assert report.totals_by_currency == {"USD": Decimal("130.00"), "EUR": Decimal("20.00")}
    assert report.inflow_by_currency == {"USD": Decimal("100.00")}
    assert report.outflow_by_currency == {"USD": Decimal("30.00"), "EUR": Decimal("20.00")}


def test_outliers_compare_inflows_with_inflows_only() -> None:
    payments = [make_row(i, amount="100.00") for i in range(1, 7)]
    collection = make_row(7, amount="90000.00", direction="INFLOW")

    assert _codes([*payments, collection]) == []
