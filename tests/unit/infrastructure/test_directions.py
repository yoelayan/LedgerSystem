"""Inflow or outflow: only ever from something explicit."""

import pytest

from ledger.domain.value_objects import Direction, DirectionSource
from ledger.infrastructure.analysis.directions import (
    direction_share,
    parse_direction,
    resolve_directions,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Egreso", Direction.OUTFLOW),
        ("CARGO", Direction.OUTFLOW),
        ("Débito", Direction.OUTFLOW),
        ("D", Direction.OUTFLOW),
        ("out", Direction.OUTFLOW),
        ("Ingreso", Direction.INFLOW),
        ("abono", Direction.INFLOW),
        ("Crédito.", Direction.INFLOW),
        ("C", Direction.INFLOW),
        ("Nómina", None),
        ("", None),
    ],
)
def test_parse_direction(raw: str, expected: Direction | None) -> None:
    assert parse_direction(raw) is expected


def test_a_column_decides_and_an_agreeing_sign_is_dropped() -> None:
    result = resolve_directions(
        ["10.00", "-20.00", "-30.00", "5.00"],
        ["Ingreso", "Cargo", "Abono", "???"],
        signed_amounts=False,
    )

    assert result.source is DirectionSource.COLUMN
    assert result.directions == ["INFLOW", "OUTFLOW", "INFLOW", "???"]
    # -30 labelled as an inflow is contradictory: it stays negative for the analysis.
    assert result.amounts == ["10.00", "20.00", "-30.00", "5.00"]


def test_signed_amounts_when_declared() -> None:
    result = resolve_directions(["-20.00", "+10.00", "5.00"], None, signed_amounts=True)

    assert result.source is DirectionSource.SIGNED_AMOUNTS
    assert result.amounts == ["20.00", "10.00", "5.00"]
    assert result.directions == ["OUTFLOW", "INFLOW", "INFLOW"]


def test_without_column_or_declaration_everything_is_a_payment() -> None:
    result = resolve_directions(["-40.00", "10.00"], None, signed_amounts=False)

    assert result.source is DirectionSource.DEFAULT
    assert result.directions == ["OUTFLOW", "OUTFLOW"]
    assert result.amounts == ["-40.00", "10.00"]  # the sign is not a direction here


def test_direction_share() -> None:
    assert direction_share(["Ingreso", "Egreso", "x", ""]) == pytest.approx(2 / 3)
    assert direction_share(["", " "]) == 0.0
