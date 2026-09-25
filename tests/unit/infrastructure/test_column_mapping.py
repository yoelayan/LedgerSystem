"""Column identification: the trained header model plus content profiling."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from ledger.domain.value_objects import MatchMethod
from ledger.infrastructure.analysis.column_mapping import (
    VOCABULARY_PATH,
    HeaderClassifier,
    VectorColumnMapper,
    default_classifier,
    normalise_header,
)

mapper = VectorColumnMapper()


def _map(columns: Sequence[str], *rows: Sequence[str]) -> dict[str, tuple[str, MatchMethod]]:
    samples = {column: [row[i] for row in rows] for i, column in enumerate(columns)}
    mapping = mapper.map(columns, samples)
    return {m.field: (m.source_column, m.method) for m in mapping.matches}


def test_normalise_header() -> None:
    assert normalise_header("  Nº de Cuenta_Destino ") == "no cuenta destino"
    assert normalise_header("F. Operación") == "f operacion"
    assert normalise_header("de") == "de"  # only stopwords: keep them rather than nothing


def test_canonical_headers_are_exact() -> None:
    result = _map(
        ["external_id", "account", "amount", "currency", "value_date"],
        ["TX-1", "ACC-1", "10.00", "USD", "2026-09-01"],
    )

    assert {method for _, method in result.values()} == {MatchMethod.EXACT}


def test_spanish_bank_export() -> None:
    result = _map(
        ["Referencia", "Nº de cuenta", "Importe neto (EUR)", "Moneda", "Fecha valor", "Concepto"],
        ["OP-1", "ES9121000418450200051332", "1250.00", "EUR", "2026-09-01", "Nómina"],
    )

    assert result == {
        "external_id": ("Referencia", MatchMethod.VOCABULARY),
        "account": ("Nº de cuenta", MatchMethod.VOCABULARY),
        "amount": ("Importe neto (EUR)", MatchMethod.SIMILARITY),
        "currency": ("Moneda", MatchMethod.VOCABULARY),
        "value_date": ("Fecha valor", MatchMethod.VOCABULARY),
    }


def test_typos_and_unseen_variants_are_recognised_by_similarity() -> None:
    result = _map(
        ["Numero de Transaccion", "Cuenta del cliente", "Montos", "Divsa", "Fcha valor"],
        ["A1", "ACC-99881", "10.50", "MXN", "2026-09-01"],
    )

    assert {field: column for field, (column, _) in result.items()} == {
        "external_id": "Numero de Transaccion",
        "account": "Cuenta del cliente",
        "amount": "Montos",
        "currency": "Divsa",
        "value_date": "Fcha valor",
    }


def test_meaningless_headers_fall_back_to_the_values() -> None:
    rows = [
        [f"TX-{i}", "ES9121000418450200051332", f"{i * 10}.50", "EUR", f"2026-09-0{i}"]
        for i in range(1, 6)
    ]

    result = _map(["col1", "col2", "col3", "col4", "col5"], *rows)

    assert result == {
        "external_id": ("col1", MatchMethod.CONTENT),
        "account": ("col2", MatchMethod.CONTENT),
        "amount": ("col3", MatchMethod.CONTENT),
        "currency": ("col4", MatchMethod.CONTENT),
        "value_date": ("col5", MatchMethod.CONTENT),
    }


def test_unrelated_columns_are_not_forced_into_a_field() -> None:
    mapping = mapper.map(["foo", "bar"], {"foo": ["x"], "bar": ["y"]})

    assert mapping.matches == ()
    assert mapping.ignored_columns == ("foo", "bar")


def test_a_column_feeds_at_most_one_field() -> None:
    mapping = mapper.map(["Fecha"], {"Fecha": ["2026-09-01"]})

    assert [m.field for m in mapping.matches] == ["value_date"]


def test_empty_values_carry_no_signal() -> None:
    assert _map(["col1"], [""]) == {}


@pytest.mark.parametrize(
    ("values", "field"),
    [
        (["01/09/2026", "15.09.2026"], "value_date"),
        (["ES91 2100 0418 4502 0005 1332"], "account"),
    ],
)
def test_content_profiling(values: list[str], field: str) -> None:
    mapping = mapper.map(["x"], {"x": values})

    assert [m.field for m in mapping.matches] == [field]


def test_impossible_dates_are_not_dates() -> None:
    mapping = mapper.map(["x"], {"x": ["2026-13-45", "2026-99-99"]})

    assert mapping.column_for("value_date") is None


def test_classifier_reports_the_nearest_known_header() -> None:
    prediction = default_classifier().predict("Importe neto (EUR)")["amount"]

    assert prediction.nearest == "importe neto"
    assert prediction.similarity > 0.9


def test_vocabulary_file_is_the_training_data(tmp_path: Path) -> None:
    vocabulary = tmp_path / "vocabulary.json"
    vocabulary.write_text(json.dumps({"amount": ["betrag"]}), encoding="utf-8")
    classifier = HeaderClassifier.from_file(vocabulary)

    mapping = VectorColumnMapper(classifier).map(["Betrag"], {"Betrag": ["x"]})

    assert mapping.column_for("amount") == "Betrag"


def test_bundled_vocabulary_covers_every_field() -> None:
    raw = json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))

    assert all(
        raw[field] for field in ("external_id", "account", "amount", "currency", "value_date")
    )
