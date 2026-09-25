"""CSV ingestion with pandas.

Only *structural* problems are rejected here (not a CSV, missing columns, empty, too big).
Row-level problems (bad amounts, duplicates...) are kept as raw strings so that the
analysis step can report every one of them instead of failing on the first.
"""

import io

import pandas as pd

from ledger.domain.exceptions import BatchTooLargeError, EmptyBatchError, MalformedDatasetError
from ledger.domain.value_objects import MAX_ROWS_PER_BATCH, REQUIRED_COLUMNS, RawTransactionRow


class PandasCsvParser:
    def parse(self, content: bytes) -> list[RawTransactionRow]:
        if not content.strip():
            raise EmptyBatchError()
        try:
            frame = pd.read_csv(
                io.BytesIO(content),
                dtype=str,
                keep_default_na=False,
                encoding="utf-8",
            )
        except UnicodeDecodeError as exc:
            raise MalformedDatasetError("file is not valid UTF-8 text.") from exc
        except pd.errors.EmptyDataError as exc:
            raise EmptyBatchError() from exc
        except pd.errors.ParserError as exc:
            raise MalformedDatasetError(f"CSV could not be parsed ({exc}).") from exc

        frame.columns = [str(column).strip().lower() for column in frame.columns]
        missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise MalformedDatasetError(
                f"missing required columns {missing}.", missing_columns=missing
            )
        if frame.empty:
            raise EmptyBatchError()
        if len(frame) > MAX_ROWS_PER_BATCH:
            raise BatchTooLargeError(len(frame), MAX_ROWS_PER_BATCH)

        # Short rows yield NaN for the absent trailing fields: normalise them to "" so the
        # analyzer reports them as MISSING_VALUE instead of crashing on a non-string.
        frame = frame[list(REQUIRED_COLUMNS)].fillna("").apply(lambda column: column.str.strip())
        return [
            RawTransactionRow(row_number=position, **record)
            for position, record in enumerate(frame.to_dict(orient="records"), start=1)
        ]
