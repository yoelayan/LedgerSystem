import pytest

from ledger.domain.exceptions import BatchTooLargeError, EmptyBatchError, MalformedDatasetError
from ledger.infrastructure.analysis import csv_parser
from ledger.infrastructure.analysis.csv_parser import PandasCsvParser
from tests.factories import CLEAN_CSV, csv_bytes

parser = PandasCsvParser()


def test_parses_rows_as_raw_strings() -> None:
    rows = parser.parse(CLEAN_CSV).rows

    assert [r.row_number for r in rows] == [1, 2, 3]
    assert rows[1].external_id == "TX-2"
    assert rows[1].amount == "150.50"
    assert rows[2].currency == "EUR"


def test_normalises_headers_and_strips_values() -> None:
    content = csv_bytes(
        " TX-1 ,ACC-1, 10.00 ,usd,2026-09-01",
        header=" External_ID ,ACCOUNT,Amount,Currency ,value_date",
    )

    [row] = parser.parse(content).rows

    assert row.external_id == "TX-1"
    assert row.amount == "10.00"
    assert row.currency == "usd"  # normalising values is the analyzer's job, not the parser's


def test_keeps_invalid_values_for_the_analyzer_to_report() -> None:
    [row] = parser.parse(csv_bytes("TX-1,,not-a-number,XYZ,yesterday")).rows

    assert row.account == ""
    assert row.amount == "not-a-number"


def test_short_rows_become_empty_values() -> None:
    [row] = parser.parse(csv_bytes("TX-1,ACC-1,10.00")).rows

    assert row.currency == ""
    assert row.value_date == ""


def test_extra_columns_are_ignored() -> None:
    header = "external_id,account,amount,currency,value_date,memo"

    [row] = parser.parse(csv_bytes("TX-1,ACC-1,10.00,USD,2026-09-01,note", header=header)).rows

    assert row.external_id == "TX-1"


def test_missing_columns_are_reported() -> None:
    content = b"external_id,amount\nTX-1,10.00\n"

    with pytest.raises(MalformedDatasetError) as exc_info:
        parser.parse(content)

    assert exc_info.value.context["missing_columns"] == ["account", "currency", "value_date"]


@pytest.mark.parametrize("content", [b"", b"   \n\n  ", b"\xef\xbb\xbf"])
def test_blank_file_is_empty(content: bytes) -> None:
    with pytest.raises(EmptyBatchError):
        parser.parse(content)


def test_header_only_is_empty() -> None:
    with pytest.raises(EmptyBatchError):
        parser.parse(csv_bytes())


def test_ragged_rows_are_malformed() -> None:
    with pytest.raises(MalformedDatasetError, match="could not be parsed"):
        parser.parse(
            csv_bytes("TX-1,ACC-1,10.00,USD,2026-09-01", "TX-2,ACC-2,10.00,USD,2026-09-01,x,y,z")
        )


def test_non_utf8_content_is_malformed() -> None:
    with pytest.raises(MalformedDatasetError, match="UTF-8"):
        parser.parse(csv_bytes("TX-1,ACC-1,10.00,USD,2026-09-01") + b"\nTX-2,\xe9\xff,1,USD,x")


def test_too_many_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(csv_parser, "MAX_ROWS_PER_BATCH", 2)

    with pytest.raises(BatchTooLargeError):
        parser.parse(CLEAN_CSV)


def test_reports_how_each_column_was_identified() -> None:
    dataset = parser.parse(CLEAN_CSV)

    assert dataset.column_mapping.column_for("amount") == "amount"
    assert dataset.column_mapping.ignored_columns == ()


def test_semicolon_separated_file_with_bom_and_other_headers() -> None:
    content = (
        "﻿Referencia;Nº de cuenta;Importe;Moneda;Fecha valor;Concepto\n"
        "OP-1;ES9121000418450200051332;12.50;EUR;2026-09-01;Nómina, septiembre\n"
    ).encode()

    dataset = parser.parse(content)

    [row] = dataset.rows
    assert (row.external_id, row.amount, row.currency) == ("OP-1", "12.50", "EUR")
    assert dataset.column_mapping.ignored_columns == ("Concepto",)


def test_tab_separated_file() -> None:
    content = (
        b"external_id\taccount\tamount\tcurrency\tvalue_date\nTX-1\tACC-1\t1.00\tUSD\t2026-09-01\n"
    )

    [row] = parser.parse(content).rows

    assert row.account == "ACC-1"


def test_unidentified_columns_list_what_the_file_has() -> None:
    with pytest.raises(MalformedDatasetError) as exc_info:
        parser.parse(b"foo,bar\n1,2\n")

    assert exc_info.value.context["available_columns"] == ["foo", "bar"]


def test_unknown_delimiter_falls_back_to_comma() -> None:
    with pytest.raises(MalformedDatasetError):
        parser.parse(b"justonecolumn\nvalue\n")
