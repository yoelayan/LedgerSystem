"""CSV ingestion with pandas.

Only *structural* problems are rejected here (not a CSV, fields that cannot be identified,
empty, too big). Row-level problems (bad amounts, duplicates...) are kept as raw strings
so that the analysis step can report every one of them instead of failing on the first.

Files do not need our exact layout: the delimiter is sniffed (`,` `;` tab `|`), a UTF-8
BOM is accepted (Excel adds one), a `ColumnMapper` works out which column holds each
field ("Importe" -> amount, "Moneda" -> currency...), and local amount and date formats
are rewritten into the canonical ones ("1.250,50" -> "1250.50", "01/09/2026" ->
"2026-09-01"), keeping the values as received on each row.
"""

import csv
import io
from typing import Final

import pandas as pd

from ledger.domain.exceptions import BatchTooLargeError, EmptyBatchError, MalformedDatasetError
from ledger.domain.value_objects import (
    MAX_ROWS_PER_BATCH,
    REQUIRED_COLUMNS,
    ColumnMapping,
    ParsedDataset,
    RawTransactionRow,
)
from ledger.infrastructure.analysis.column_mapping import (
    SAMPLE_SIZE,
    ColumnMapper,
    VectorColumnMapper,
)
from ledger.infrastructure.analysis.value_normalization import (
    Normalisation,
    normalise_amounts,
    normalise_dates,
)

DELIMITERS: Final = ",;\t|"
SNIFF_BYTES: Final = 16 * 1024


class PandasCsvParser:
    def __init__(self, mapper: ColumnMapper | None = None) -> None:
        self._mapper = mapper or VectorColumnMapper()

    def parse(self, content: bytes) -> ParsedDataset:
        if not content.strip():
            raise EmptyBatchError()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MalformedDatasetError("file is not valid UTF-8 text.") from exc
        try:
            frame = pd.read_csv(
                io.StringIO(text),
                sep=_sniff_delimiter(text),
                dtype=str,
                keep_default_na=False,
            )
        except pd.errors.EmptyDataError as exc:
            raise EmptyBatchError() from exc
        except pd.errors.ParserError as exc:
            raise MalformedDatasetError(f"CSV could not be parsed ({exc}).") from exc

        frame.columns = [str(column).strip() for column in frame.columns]
        columns = list(frame.columns)
        # Short rows yield NaN for the absent trailing fields: normalise them to "" so the
        # analyzer reports them as MISSING_VALUE instead of crashing on a non-string.
        frame = frame.fillna("").apply(lambda column: column.str.strip())
        samples = {column: frame[column].head(SAMPLE_SIZE).tolist() for column in columns}
        mapping = self._mapper.map(columns, samples)
        if mapping.missing_fields:
            missing = list(mapping.missing_fields)
            raise MalformedDatasetError(
                f"could not identify the columns for {missing}.",
                missing_columns=missing,
                available_columns=columns,
            )
        if frame.empty:
            raise EmptyBatchError()
        if len(frame) > MAX_ROWS_PER_BATCH:
            raise BatchTooLargeError(len(frame), MAX_ROWS_PER_BATCH)

        selected = frame[[mapping.column_for(field) for field in REQUIRED_COLUMNS]]
        selected.columns = list(REQUIRED_COLUMNS)
        records = selected.to_dict(orient="records")
        normalisations = {
            "amount": normalise_amounts([r["amount"] for r in records]),
            "value_date": normalise_dates([r["value_date"] for r in records]),
        }
        rows = tuple(
            _row(position, record, {f: n.values[position - 1] for f, n in normalisations.items()})
            for position, record in enumerate(records, start=1)
        )
        return ParsedDataset(rows=rows, column_mapping=_with_formats(mapping, normalisations))


def _row(position: int, record: dict[str, str], normalised: dict[str, str]) -> RawTransactionRow:
    original = {f: record[f] for f, value in normalised.items() if value != record[f]}
    return RawTransactionRow(
        row_number=position, **{**record, **normalised}, original_values=original
    )


def _with_formats(
    mapping: ColumnMapping, normalisations: dict[str, Normalisation]
) -> ColumnMapping:
    matches = tuple(
        match.model_copy(
            update={
                "value_format": normalisations[match.field].format_label,
                "format_ambiguous": normalisations[match.field].ambiguous,
            }
        )
        if match.field in normalisations
        else match
        for match in mapping.matches
    )
    return mapping.model_copy(update={"matches": matches})


def _sniff_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:SNIFF_BYTES], delimiters=DELIMITERS).delimiter
    except csv.Error:
        return ","
