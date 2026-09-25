"""Rewrite amounts and dates from local formats into the canonical ones.

The analyzer expects `1250.50` and `2026-09-01`; bank exports say `1.250,50 €` or
`01/09/2026`. The format is detected **per column**, never per value: "1.250" means 1250
in a column that also holds "980,50", and 1.25 in one that holds "980.50". Deciding value
by value would let a single file mix both readings.

When the column gives no way to decide, values are left untouched so that the analysis
reports them as errors. Misreading an amount by a factor of 1000 is not a risk worth
taking. Dates are different: `03/04/2026` fits both day/month and month/day, and the
day-first convention used across Europe and Latin America is assumed. The assumption is
flagged so the approver sees it.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

# Currency symbols and ISO codes around the number: "€ 1.250,50", "1,250.50 USD", "12EUR".
_CURRENCY_SYMBOLS = re.compile(r"[€$£¥]")
_CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}\s*(?=[\d+(-])|(?<=[\d)])\s*[A-Za-z]{3}$")
# Separators that can only ever group thousands: spaces (\s includes the non-breaking
# ones spreadsheets use) and apostrophes.
_GROUPING = re.compile(r"[\s']")
_TIME_SUFFIX = re.compile(r"^(\S+?)(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")


@dataclass(frozen=True, slots=True)
class AmountFormat:
    label: str  # how the format is shown to people, e.g. "1.234,56"
    decimal: str
    thousands: str
    pattern: re.Pattern[str]

    def normalise(self, raw: str) -> str | None:
        """Canonical amount, or None when the value does not fit this format."""
        cleaned = _clean_amount(raw)
        if not self.pattern.match(cleaned):
            return None
        return cleaned.replace(self.thousands, "").replace(self.decimal, ".")


DOT_DECIMAL: Final = AmountFormat(
    "1,234.56", ".", ",", re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
)
COMMA_DECIMAL: Final = AmountFormat(
    "1.234,56", ",", ".", re.compile(r"^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$")
)


@dataclass(frozen=True, slots=True)
class DateFormat:
    label: str
    pattern: str  # strptime directive

    def normalise(self, raw: str) -> str | None:
        """Canonical ISO date, or None when the value does not fit this format."""
        match = _TIME_SUFFIX.match(raw.strip())
        if not match:
            return None
        try:
            parsed = datetime.strptime(match.group(1), self.pattern)
        except ValueError:
            return None
        return parsed.date().isoformat()


ISO_DATE: Final = DateFormat("AAAA-MM-DD", "%Y-%m-%d")
# Preference order when several formats fit equally well: ISO, then day-first.
DATE_FORMATS: Final = (
    ISO_DATE,
    DateFormat("AAAA/MM/DD", "%Y/%m/%d"),
    DateFormat("DD/MM/AAAA", "%d/%m/%Y"),
    DateFormat("MM/DD/AAAA", "%m/%d/%Y"),
    DateFormat("DD-MM-AAAA", "%d-%m-%Y"),
    DateFormat("MM-DD-AAAA", "%m-%d-%Y"),
    DateFormat("DD.MM.AAAA", "%d.%m.%Y"),
)
_MONTH_FIRST_TWIN: Final = {
    "%d/%m/%Y": "%m/%d/%Y",
    "%d-%m-%Y": "%m-%d-%Y",
}


@dataclass(frozen=True, slots=True)
class Normalisation:
    """Outcome for one column."""

    values: list[str]  # canonical where convertible, untouched otherwise
    format_label: str | None  # None when nothing had to change
    ambiguous: bool = False


def normalise_amounts(values: Sequence[str]) -> Normalisation:
    fmt = _detect_amount_format(values)
    if fmt is None:
        return Normalisation(list(values), None)
    return _apply(values, fmt.normalise, fmt.label)


def normalise_dates(values: Sequence[str]) -> Normalisation:
    present = [v for v in values if v.strip()]
    counts = {fmt: sum(fmt.normalise(v) is not None for v in present) for fmt in DATE_FORMATS}
    best = max(DATE_FORMATS, key=lambda fmt: counts[fmt])  # first one wins ties
    if counts[best] == 0:
        return Normalisation(list(values), None)
    twin = _MONTH_FIRST_TWIN.get(best.pattern)
    ambiguous = twin is not None and any(
        f.pattern == twin and counts[f] == counts[best] for f in DATE_FORMATS
    )
    result = _apply(values, best.normalise, best.label)
    return Normalisation(
        result.values, result.format_label, ambiguous and bool(result.format_label)
    )


def _detect_amount_format(values: Sequence[str]) -> AmountFormat | None:
    fits_dot = fits_comma = readings_differ = False
    for raw in values:
        cleaned = _clean_amount(raw)
        dot = DOT_DECIMAL.pattern.match(cleaned) is not None
        comma = COMMA_DECIMAL.pattern.match(cleaned) is not None
        fits_dot |= dot and not comma
        fits_comma |= comma and not dot
        # "1.250" or "1,250": valid in both formats, with values 1000 times apart.
        readings_differ |= (
            dot and comma and DOT_DECIMAL.normalise(raw) != COMMA_DECIMAL.normalise(raw)
        )
    if fits_dot and fits_comma:
        return None  # the column mixes both formats
    if fits_comma:
        return COMMA_DECIMAL
    if fits_dot or not readings_differ:
        return DOT_DECIMAL
    return None  # every separator is ambiguous: refuse to guess


def _clean_amount(raw: str) -> str:
    value = _CURRENCY_CODE.sub("", _CURRENCY_SYMBOLS.sub("", raw.strip())).strip()
    value = _GROUPING.sub("", value)
    if value.startswith("(") and value.endswith(")"):  # accounting notation for negatives
        value = f"-{value[1:-1]}"
    return value


def _apply(
    values: Sequence[str], convert: Callable[[str], str | None], label: str
) -> Normalisation:
    converted = [convert(v) if v.strip() else None for v in values]
    result = [new if new is not None else old for old, new in zip(values, converted, strict=True)]
    changed = any(new != old for old, new in zip(values, result, strict=True))
    return Normalisation(result, label if changed else None)
