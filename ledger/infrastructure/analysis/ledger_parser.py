"""Read an ERP ledger export (libro mayor / diario) into typed entries for reconciliation.

The export is expected to hold the entries of the organisation's **bank account** (e.g.
cuenta 572 "Bancos" in the Spanish PGC). There, a debit (Debe, "cargo") means money came
in and a credit (Haber, "abono") means money went out. An export of the counterpart
accounts (suppliers, customers) reads the other way round; `invert` flips it.

Accepted layouts, recognised by the same header model as batches:

* Two amount columns, Debe / Haber (debit / credit).
* One amount column plus a column saying D/H, Debe/Haber, Cargo/Abono or Ingreso/Egreso.
* One signed amount column: positive = debit, negative = credit.

Only the date and the amount(s) are mandatory. The reference (document number,
payment reference...) greatly improves matching. When there is no currency column,
the currency chosen on upload applies to every entry.

Lines that cannot be read are reported one by one, as problems, without stopping the
rest of the file from being reconciled.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from unidecode import unidecode

from ledger.domain.analytics.reconciliation import LedgerEntry, LedgerProblem, ParsedLedger
from ledger.domain.exceptions import MalformedDatasetError
from ledger.domain.value_objects import ColumnMapping, Direction
from ledger.infrastructure.analysis.column_mapping import ColumnMapper, VectorColumnMapper
from ledger.infrastructure.analysis.csv_parser import read_frame, samples_of
from ledger.infrastructure.analysis.directions import parse_direction
from ledger.infrastructure.analysis.value_normalization import normalise_amounts, normalise_dates

LEDGER_FIELDS: Final = (
    "external_id",
    "value_date",
    "amount",
    "debit",
    "credit",
    "currency",
    "direction",
    "description",
)
_DEBIT_WORDS: Final = frozenset({"debe", "d", "debit", "dr", "cargo", "debito", "deudor"})
_CREDIT_WORDS: Final = frozenset(
    {"haber", "h", "credit", "cr", "c", "abono", "credito", "acreedor"}
)


class PandasLedgerParser:
    def __init__(self, mapper: ColumnMapper | None = None) -> None:
        self._mapper = mapper or VectorColumnMapper(fields=LEDGER_FIELDS)

    def parse(self, content: bytes, *, default_currency: str, invert: bool = False) -> ParsedLedger:
        frame = read_frame(content)
        columns = list(frame.columns)
        mapping = self._mapper.map(columns, samples_of(frame))
        _require(mapping, columns)

        def column(field: str) -> list[str] | None:
            name = mapping.column_for(field)
            return frame[name].tolist() if name is not None else None

        dates = normalise_dates(column("value_date") or [])
        amounts = _normalised(column("amount"))
        debits = _normalised(column("debit"))
        credits = _normalised(column("credit"))
        directions = column("direction")
        references = column("external_id")
        currencies = column("currency")
        descriptions = column("description")
        debit_is_inflow = not invert
        if debits is None and credits is None and directions is None:
            _require_signs(amounts or [], columns)

        entries: list[LedgerEntry] = []
        problems: list[LedgerProblem] = []
        for index in range(len(frame)):
            row_number = index + 1
            try:
                amount, is_debit = _amount_and_side(
                    amounts[index] if amounts else None,
                    debits[index] if debits else None,
                    credits[index] if credits else None,
                    directions[index] if directions else None,
                )
                currency = _currency((currencies[index] if currencies else "") or default_currency)
                entries.append(
                    LedgerEntry(
                        row_number=row_number,
                        reference=references[index] if references else "",
                        amount=amount,
                        currency=currency,
                        entry_date=_date(dates.values[index]),
                        direction=(
                            Direction.INFLOW if is_debit == debit_is_inflow else Direction.OUTFLOW
                        ),
                        description=descriptions[index] if descriptions else "",
                    )
                )
            except _UnreadableLineError as exc:
                problems.append(LedgerProblem(row_number=row_number, message=str(exc)))
        return ParsedLedger(
            entries=tuple(entries),
            problems=tuple(problems),
            column_mapping=mapping,
            direction_rule=(
                "Debe = ingreso, Haber = egreso (cuenta de bancos)"
                if debit_is_inflow
                else "Debe = egreso, Haber = ingreso (invertido)"
            ),
        )


class _UnreadableLineError(Exception):
    pass


def _require(mapping: ColumnMapping, columns: list[str]) -> None:
    has_amount = mapping.column_for("amount") is not None or (
        mapping.column_for("debit") is not None or mapping.column_for("credit") is not None
    )
    missing = [
        field
        for field, present in (
            ("value_date", mapping.column_for("value_date") is not None),
            ("amount", has_amount),
        )
        if not present
    ]
    if missing:
        raise MalformedDatasetError(
            f"could not identify the columns for {missing}.",
            missing_columns=missing,
            available_columns=columns,
        )


def _require_signs(amounts: list[str], columns: list[str]) -> None:
    """A single unsigned amount column cannot tell debits from credits: refuse to guess."""
    if not any(a.startswith("-") for a in amounts):
        raise MalformedDatasetError(
            "cannot tell debits from credits: add Debe/Haber columns, a D/H column "
            "or signed amounts.",
            missing_columns=["direction"],
            available_columns=columns,
        )


def _normalised(values: list[str] | None) -> list[str] | None:
    return normalise_amounts(values).values if values is not None else None


def _amount_and_side(
    amount: str | None, debit: str | None, credit: str | None, direction: str | None
) -> tuple[Decimal, bool]:
    """(positive amount, whether it is a debit)."""
    if debit is not None or credit is not None:
        debit_value = _decimal(debit)
        credit_value = _decimal(credit)
        if debit_value and credit_value:
            raise _UnreadableLineError("La línea tiene importe en el Debe y en el Haber.")
        if debit_value:
            return abs(debit_value), debit_value > 0
        if credit_value:
            return abs(credit_value), credit_value < 0
        if amount is None:
            raise _UnreadableLineError("La línea no tiene importe ni en el Debe ni en el Haber.")
    value = _decimal(amount)
    if not value:
        raise _UnreadableLineError(f"Importe no válido: '{amount or ''}'.")
    if direction is None:
        return abs(value), value > 0  # signed ledger amount: positive = debit
    side = _side(direction)
    if side is None:
        raise _UnreadableLineError(f"No se reconoce si '{direction}' es Debe o Haber.")
    return abs(value), side


def _side(raw: str) -> bool | None:
    """True for a debit, False for a credit, None when unrecognised."""
    word = unidecode(raw).strip().lower().rstrip(".")
    if word in _DEBIT_WORDS:
        return True
    if word in _CREDIT_WORDS:
        return False
    # Money-flow words (Ingreso/Egreso...) mean the same in the bank account's books.
    direction = parse_direction(raw)
    return None if direction is None else direction is Direction.INFLOW


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _UnreadableLineError(f"Fecha no válida: '{value}'.") from exc


def _currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise _UnreadableLineError(f"Divisa no válida: '{value}'.")
    return code


def _decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise _UnreadableLineError(f"Importe no válido: '{value}'.") from exc
    if not parsed.is_finite():
        raise _UnreadableLineError(f"Importe no válido: '{value}'.")
    return parsed
