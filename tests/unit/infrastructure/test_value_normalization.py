"""Local amount and date formats, decided per column."""

import pytest

from ledger.infrastructure.analysis.value_normalization import normalise_amounts, normalise_dates


class TestAmounts:
    @pytest.mark.parametrize(
        ("values", "expected", "label"),
        [
            (["1.250,50", "980,50"], ["1250.50", "980.50"], "1.234,56"),
            (["1,250.50", "980.50"], ["1250.50", "980.50"], "1,234.56"),
            (["1 250,50", "2\u00a0000,00"], ["1250.50", "2000.00"], "1.234,56"),
            (["12'345.60", "5.00"], ["12345.60", "5.00"], "1,234.56"),
            (["€ 1.250,50", "7,25 €", "EUR 3,5"], ["1250.50", "7.25", "3.5"], "1.234,56"),
            (["-€12.50", "USD -3.00", "12.00EUR"], ["-12.50", "-3.00", "12.00"], "1,234.56"),
            (["(12,00)", "5,00"], ["-12.00", "5.00"], "1.234,56"),  # accounting negative
        ],
    )
    def test_local_formats_become_canonical(
        self, values: list[str], expected: list[str], label: str
    ) -> None:
        result = normalise_amounts(values)

        assert result.values == expected
        assert result.format_label == label

    def test_the_column_decides_what_an_ambiguous_value_means(self) -> None:
        assert normalise_amounts(["1.250", "980,50"]).values == ["1250", "980.50"]
        assert normalise_amounts(["1,250", "980.50"]).values == ["1250", "980.50"]

    @pytest.mark.parametrize(
        "values",
        [
            ["1,250", "2,500"],  # 1250 or 1.25? nothing in the column tells
            ["1250.00", "12,50"],  # the column mixes both formats
        ],
    )
    def test_undecidable_columns_are_left_for_the_analyzer_to_reject(
        self, values: list[str]
    ) -> None:
        result = normalise_amounts(values)

        assert result.values == values
        assert result.format_label is None

    def test_canonical_values_report_no_conversion(self) -> None:
        result = normalise_amounts(["1250.00", "99.999", "100"])

        assert result.values == ["1250.00", "99.999", "100"]
        assert result.format_label is None

    def test_unparseable_and_empty_values_are_kept(self) -> None:
        assert normalise_amounts(["abc", "", "12,5"]).values == ["abc", "", "12.5"]


class TestDates:
    @pytest.mark.parametrize(
        ("values", "expected", "label"),
        [
            (["01/09/2026", "15/09/2026"], ["2026-09-01", "2026-09-15"], "DD/MM/AAAA"),
            (["09/15/2026", "03/04/2026"], ["2026-09-15", "2026-03-04"], "MM/DD/AAAA"),
            (["1.9.2026"], ["2026-09-01"], "DD.MM.AAAA"),
            (["15-09-2026"], ["2026-09-15"], "DD-MM-AAAA"),
            (["2026/09/01"], ["2026-09-01"], "AAAA/MM/DD"),
            (
                ["2026-09-01 00:00:00", "2026-09-02T10:30"],
                ["2026-09-01", "2026-09-02"],
                "AAAA-MM-DD",
            ),
        ],
    )
    def test_local_formats_become_iso(
        self, values: list[str], expected: list[str], label: str
    ) -> None:
        result = normalise_dates(values)

        assert result.values == expected
        assert result.format_label == label
        assert not result.ambiguous

    def test_day_first_is_assumed_and_flagged_when_both_orders_fit(self) -> None:
        result = normalise_dates(["03/04/2026", "05/06/2026"])

        assert result.values == ["2026-04-03", "2026-06-05"]
        assert result.ambiguous

    @pytest.mark.parametrize(
        "values",
        [["2026-09-01", "2026-13-40"], ["yesterday"], ["2026-09-01 por la tarde"], ["", "  "]],
    )
    def test_canonical_or_unparseable_dates_are_untouched(self, values: list[str]) -> None:
        result = normalise_dates(values)

        assert result.values == values
        assert result.format_label is None
