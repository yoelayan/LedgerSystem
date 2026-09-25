"""ERP ledger exports: Debe/Haber, D/H or signed amounts, read into typed entries."""

from datetime import date
from decimal import Decimal

import pytest

from ledger.domain.exceptions import MalformedDatasetError
from ledger.domain.value_objects import Direction
from ledger.infrastructure.analysis.ledger_parser import PandasLedgerParser

parser = PandasLedgerParser()


def _parse(text: str, **options: object) -> object:
    return parser.parse(text.encode(), default_currency=options.pop("currency", "EUR"), **options)  # type: ignore[arg-type]


def test_debe_haber_columns_in_spanish_format() -> None:
    ledger = _parse(
        "Fecha asiento;Documento;Concepto;Debe;Haber\n"
        "01/09/2026;OP-1;Pago proveedor;;1.250,00\n"
        "02/09/2026;COB-7;Cobro cliente;3.000,00;0,00\n"
    )

    pay, collect = ledger.entries
    assert (pay.reference, pay.amount, pay.direction) == (
        "OP-1",
        Decimal("1250.00"),
        Direction.OUTFLOW,
    )
    assert (collect.entry_date, collect.direction) == (date(2026, 9, 2), Direction.INFLOW)
    assert pay.description == "Pago proveedor"
    assert pay.currency == "EUR"
    assert "Debe = ingreso" in ledger.direction_rule


def test_invert_for_counterpart_accounts() -> None:
    ledger = _parse("Fecha;Debe;Haber\n2026-09-01;;50.00\n", invert=True)

    assert ledger.entries[0].direction is Direction.INFLOW
    assert "invertido" in ledger.direction_rule


def test_side_column_and_currency_column() -> None:
    ledger = _parse(
        "Date,Ref,Amount,D/H,Currency\n"
        "2026-09-01,A,50.00,H,usd\n"
        "2026-09-02,B,60.00,Debe,USD\n"
        "2026-09-03,C,70.00,Egreso,USD\n"
        "2026-09-04,D,80.00,??,USD\n"
    )

    assert [(e.reference, e.direction) for e in ledger.entries] == [
        ("A", Direction.OUTFLOW),
        ("B", Direction.INFLOW),
        ("C", Direction.OUTFLOW),
    ]
    assert ledger.entries[0].currency == "USD"
    assert "no se reconoce" in ledger.problems[0].message.lower()


def test_signed_amount_column() -> None:
    ledger = _parse("Fecha,Referencia,Importe\n2026-09-01,A,-10.00\n2026-09-02,B,15.00\n")

    assert [(e.amount, e.direction) for e in ledger.entries] == [
        (Decimal("10.00"), Direction.OUTFLOW),
        (Decimal("15.00"), Direction.INFLOW),
    ]


def test_unsigned_single_amount_is_refused() -> None:
    with pytest.raises(MalformedDatasetError, match="debits from credits"):
        _parse("Fecha,Referencia,Importe\n2026-09-01,A,10.00\n")


def test_missing_date_or_amount() -> None:
    with pytest.raises(MalformedDatasetError) as exc_info:
        _parse("foo,bar\n1,2\n")

    assert exc_info.value.context["missing_columns"] == ["value_date", "amount"]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("2026-09-01;10,00;5,00", "Debe y en el Haber"),
        ("2026-09-01;;", "ni en el Debe ni en el Haber"),
        ("2026-09-01;abc;", "Importe no válido"),
        ("2026-09-01;Infinity;", "Importe no válido"),
        ("31/02/2026;10,00;", "Fecha no válida"),
    ],
)
def test_unreadable_lines_are_reported_not_fatal(line: str, message: str) -> None:
    ledger = _parse(f"Fecha;Debe;Haber\n2026-09-02;1,00;\n{line}\n")

    assert len(ledger.entries) == 1
    [problem] = ledger.problems
    assert problem.row_number == 2
    assert message in problem.message


def test_invalid_currency_is_a_problem() -> None:
    ledger = _parse("Fecha;Debe;Haber;Moneda\n2026-09-02;1,00;;EURO\n")

    assert ledger.problems[0].message == "Divisa no válida: 'EURO'."


def test_empty_debit_credit_falls_back_to_an_amount_column() -> None:
    ledger = _parse("Fecha;Debe;Haber;Importe\n2026-09-02;;;-5,00\n")

    assert ledger.entries[0].direction is Direction.OUTFLOW


def test_zero_amount_is_unreadable() -> None:
    ledger = _parse("Fecha,Importe\n2026-09-02,-1.00\n2026-09-03,0.00\n")

    assert "Importe no válido" in ledger.problems[0].message
