"""Decide whether each movement is an inflow (ingreso) or an outflow (egreso).

The direction is only ever taken from something explicit:

1. A column that says it: "Tipo: Ingreso/Egreso", "Cargo/Abono", "D/C", "in/out"...
   The values are read from the organisation's point of view, as a bank statement shows
   them: a "cargo" or "débito" is money going out.
2. The uploader declaring that amounts are signed: negative means outflow.
3. Neither: the batch is a batch of payments and every movement is an outflow.

The sign alone never decides. In a payments file, "-40.00" is far more likely a typo
than a collection, and treating it as money coming in would hide the mistake. Without
a declaration it stays negative and the analysis rejects it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from unidecode import unidecode

from ledger.domain.value_objects import Direction, DirectionSource

_OUTFLOW_WORDS: Final = frozenset(
    {
        "egreso", "egresos", "salida", "salidas", "cargo", "cargos", "pago", "pagos",
        "debito", "debit", "dr", "d", "out", "outflow", "retiro", "retirada", "-",
    }
)  # fmt: skip
_INFLOW_WORDS: Final = frozenset(
    {
        "ingreso", "ingresos", "entrada", "entradas", "abono", "abonos", "cobro", "cobros",
        "credito", "credit", "cr", "c", "in", "inflow", "deposito", "+",
    }
)  # fmt: skip


def parse_direction(raw: str) -> Direction | None:
    word = unidecode(raw).strip().lower().rstrip(".")
    if word in _OUTFLOW_WORDS:
        return Direction.OUTFLOW
    if word in _INFLOW_WORDS:
        return Direction.INFLOW
    return None


@dataclass(frozen=True, slots=True)
class DirectionResolution:
    amounts: list[str]  # without the sign when the sign was a direction marker
    directions: list[str]  # INFLOW / OUTFLOW, or the raw value when unrecognised
    source: DirectionSource


def resolve_directions(
    amounts: Sequence[str], direction_values: Sequence[str] | None, *, signed_amounts: bool
) -> DirectionResolution:
    """`amounts` must already be canonical ("-12.50", "1250.00")."""
    if direction_values is not None:
        directions: list[str] = []
        unsigned: list[str] = []
        for amount, raw in zip(amounts, direction_values, strict=True):
            direction = parse_direction(raw)
            directions.append(direction.value if direction else raw)
            # "-50.00" labelled as an outflow is consistent: keep 50.00. Labelled as an
            # inflow it is contradictory, so it stays negative for the analysis to reject.
            agrees = direction is Direction.OUTFLOW and amount.startswith("-")
            unsigned.append(amount[1:] if agrees else amount)
        return DirectionResolution(unsigned, directions, DirectionSource.COLUMN)
    if signed_amounts:
        return DirectionResolution(
            [a[1:] if a.startswith("-") else a.removeprefix("+") for a in amounts],
            [(Direction.OUTFLOW if a.startswith("-") else Direction.INFLOW).value for a in amounts],
            DirectionSource.SIGNED_AMOUNTS,
        )
    return DirectionResolution(
        list(amounts), [Direction.OUTFLOW.value] * len(amounts), DirectionSource.DEFAULT
    )


def direction_share(values: Sequence[str]) -> float:
    """Fraction of values that read as a direction: evidence that a column holds them."""
    present = [v for v in values if v.strip()]
    if not present:
        return 0.0
    return sum(parse_direction(v) is not None for v in present) / len(present)
