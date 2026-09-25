"""Vectorised data-quality analysis of a batch with pandas.

Blocking checks (severity ERROR -> batch is rejected automatically):
    missing values, non-numeric / non-finite amounts, amounts <= 0, more than two decimal
    places, unsupported currency, invalid ISO value date, duplicated external_id.

Non-blocking checks (severity WARNING -> shown to the approver):
    amount outliers per currency using the modified z-score (Iglewicz & Hoaglin):
        M_i = 0.6745 * |x_i - median| / MAD      flagged when M_i > 3.5
    MAD-based scores are robust: a single huge amount cannot mask itself by inflating the
    standard deviation, which is exactly the failure mode of a classic z-score.

Money is summed with Decimal, never with floats; floats are only used for the statistics.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation

import pandas as pd

from ledger.domain.value_objects import (
    MAX_FRACTION_DIGITS,
    REQUIRED_COLUMNS,
    SUPPORTED_CURRENCIES,
    AnalysisIssue,
    AnalysisReport,
    IssueCode,
    RawTransactionRow,
    Severity,
)

_MODIFIED_Z_FACTOR = 0.6745
_CENT = Decimal("0.01")


def _to_decimal(raw: str) -> Decimal | None:
    """Parse an amount; None when it is not a finite decimal number."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _has_excessive_precision(value: object) -> bool:
    # pandas may hand back NaN instead of None for unparseable cells, hence isinstance.
    if not isinstance(value, Decimal):
        return False
    exponent = value.as_tuple().exponent
    return isinstance(exponent, int) and -exponent > MAX_FRACTION_DIGITS


class PandasTransactionAnalyzer:
    def __init__(self, *, outlier_threshold: float = 3.5, min_outlier_sample: int = 5) -> None:
        self._outlier_threshold = outlier_threshold
        self._min_outlier_sample = min_outlier_sample

    def analyze(self, rows: Sequence[RawTransactionRow]) -> AnalysisReport:
        frame = pd.DataFrame(
            [row.model_dump() for row in rows], columns=["row_number", *REQUIRED_COLUMNS]
        )
        issues: list[AnalysisIssue] = []

        def flag(
            mask: pd.Series,
            code: IssueCode,
            severity: Severity,
            describe: Callable[[pd.Series], str],
        ) -> None:
            for _, row in frame.loc[mask.astype(bool)].iterrows():
                issues.append(
                    AnalysisIssue(
                        row_number=int(row["row_number"]),
                        code=code,
                        severity=severity,
                        message=describe(row),
                    )
                )

        missing = frame[list(REQUIRED_COLUMNS)].eq("")
        for column in REQUIRED_COLUMNS:
            flag(
                missing[column],
                IssueCode.MISSING_VALUE,
                Severity.ERROR,
                lambda _row, column=column: f"Column '{column}' is empty.",
            )

        decimals = frame["amount"].map(_to_decimal)
        parseable = decimals.notna()
        invalid_amount = ~parseable & ~missing["amount"]
        flag(
            invalid_amount,
            IssueCode.INVALID_AMOUNT,
            Severity.ERROR,
            lambda row: f"Amount '{row['amount']}' is not a valid decimal number.",
        )

        amounts = decimals.map(
            lambda value: float(value) if isinstance(value, Decimal) else float("nan")
        )
        non_positive = parseable & amounts.le(0)
        flag(
            non_positive,
            IssueCode.NON_POSITIVE_AMOUNT,
            Severity.ERROR,
            lambda row: f"Amount {row['amount']} must be greater than zero.",
        )

        excessive_precision = decimals.map(_has_excessive_precision).astype(bool)
        flag(
            excessive_precision,
            IssueCode.EXCESSIVE_PRECISION,
            Severity.ERROR,
            lambda row: (
                f"Amount {row['amount']} has more than {MAX_FRACTION_DIGITS} decimal places."
            ),
        )

        currency = frame["currency"].str.upper()
        unsupported_currency = ~missing["currency"] & ~currency.isin(SUPPORTED_CURRENCIES)
        flag(
            unsupported_currency,
            IssueCode.UNSUPPORTED_CURRENCY,
            Severity.ERROR,
            lambda row: f"Currency '{row['currency']}' is not supported.",
        )

        value_dates = pd.to_datetime(frame["value_date"], format="%Y-%m-%d", errors="coerce")
        invalid_date = ~missing["value_date"] & value_dates.isna()
        flag(
            invalid_date,
            IssueCode.INVALID_VALUE_DATE,
            Severity.ERROR,
            lambda row: f"Value date '{row['value_date']}' is not a valid YYYY-MM-DD date.",
        )

        duplicated = ~missing["external_id"] & frame["external_id"].duplicated(keep="first")
        flag(
            duplicated,
            IssueCode.DUPLICATE_EXTERNAL_ID,
            Severity.ERROR,
            lambda row: f"external_id '{row['external_id']}' appears more than once.",
        )

        blocking = (
            missing.any(axis=1)
            | invalid_amount
            | non_positive
            | excessive_precision
            | unsupported_currency
            | invalid_date
            | duplicated
        )
        valid = ~blocking

        flag(
            self._outliers(amounts.where(valid), currency, valid),
            IssueCode.AMOUNT_OUTLIER,
            Severity.WARNING,
            lambda row: (
                f"Amount {row['amount']} {row['currency']} is a statistical outlier "
                "for this batch; review before approving."
            ),
        )

        totals: dict[str, Decimal] = {}
        for currency_code, group in decimals[valid].groupby(currency[valid]):
            totals[str(currency_code)] = sum(group.tolist(), Decimal("0")).quantize(_CENT)

        return AnalysisReport(
            total_rows=len(frame),
            valid_rows=int(valid.sum()),
            totals_by_currency=totals,
            issues=tuple(sorted(issues, key=lambda issue: (issue.row_number, issue.code))),
        )

    def _outliers(self, values: pd.Series, currency: pd.Series, valid: pd.Series) -> pd.Series:
        by_currency = values.groupby(currency)
        median = by_currency.transform("median")
        deviation = (values - median).abs()
        mad = deviation.groupby(currency).transform("median")
        sample_size = by_currency.transform("count")
        score = _MODIFIED_Z_FACTOR * deviation / mad.where(mad > 0)
        return valid & sample_size.ge(self._min_outlier_sample) & score.gt(self._outlier_threshold)
