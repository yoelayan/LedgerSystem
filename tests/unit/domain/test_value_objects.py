from decimal import Decimal

import pytest
from pydantic import ValidationError

from ledger.domain.value_objects import (
    AnalysisIssue,
    AnalysisReport,
    ColumnMapping,
    ColumnMatch,
    IssueCode,
    MatchMethod,
    Severity,
)


def _issue(severity: Severity) -> AnalysisIssue:
    return AnalysisIssue(
        row_number=1, code=IssueCode.AMOUNT_OUTLIER, severity=severity, message="m"
    )


def test_warnings_do_not_block() -> None:
    report = AnalysisReport(
        total_rows=1,
        valid_rows=1,
        totals_by_currency={"USD": Decimal("1.00")},
        issues=(_issue(Severity.WARNING),),
    )

    assert not report.has_blocking_issues
    assert report.warnings == (_issue(Severity.WARNING),)
    assert report.blocking_issues == ()


def test_errors_block() -> None:
    report = AnalysisReport(
        total_rows=1, valid_rows=0, totals_by_currency={}, issues=(_issue(Severity.ERROR),)
    )

    assert report.has_blocking_issues
    assert report.warnings == ()


def test_valid_rows_cannot_exceed_total() -> None:
    with pytest.raises(ValidationError, match="valid_rows cannot exceed total_rows"):
        AnalysisReport(total_rows=1, valid_rows=2, totals_by_currency={})


def test_report_is_immutable() -> None:
    report = AnalysisReport(total_rows=0, valid_rows=0, totals_by_currency={})

    with pytest.raises(ValidationError):
        report.total_rows = 5  # type: ignore[misc]


class TestColumnMapping:
    mapping = ColumnMapping(
        matches=(
            ColumnMatch(
                field="amount", source_column="Importe", method=MatchMethod.VOCABULARY, confidence=1
            ),
        ),
        ignored_columns=("Concepto",),
    )

    def test_column_for(self) -> None:
        assert self.mapping.column_for("amount") == "Importe"
        assert self.mapping.column_for("currency") is None

    def test_missing_fields_keep_the_canonical_order(self) -> None:
        assert self.mapping.missing_fields == ("external_id", "account", "currency", "value_date")

    def test_confidence_is_a_ratio(self) -> None:
        with pytest.raises(ValidationError):
            ColumnMatch(field="amount", source_column="x", method=MatchMethod.CONTENT, confidence=2)
