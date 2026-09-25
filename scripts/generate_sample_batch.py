"""Generate a large, realistic transactions CSV to try the system out.

The file looks like a Spanish bank export on purpose, so that it exercises the column
identification and the local-format normalisation too: headers in Spanish, `;` as
separator, amounts like `1.250,50`, dates like `15/09/2026`, valid IBANs and a few
currencies. A handful of amounts are unusually large, so the analysis reports them as
outliers (warnings) and the batch still reaches PENDING_APPROVAL.

With `--errors N`, N rows get a blocking problem (bad amount, unsupported currency,
impossible date, missing account or duplicated reference) and the batch is rejected
automatically, listing every problem.

Usage:
    python scripts/generate_sample_batch.py                       # 5000 clean rows
    python scripts/generate_sample_batch.py --rows 5000 --errors 25 -o samples/x.csv
"""

import argparse
import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from schwifty import IBAN

HEADER = ["Referencia", "Nº de cuenta", "Importe", "Moneda", "Fecha valor", "Concepto"]
# Typical amount per currency: COP amounts are ~4000x EUR ones.
CURRENCIES = {"EUR": 850, "USD": 920, "MXN": 16_000, "COP": 3_500_000}
CURRENCY_WEIGHTS = [60, 20, 12, 8]
CONCEPTS = [
    "Nómina",
    "Alquiler",
    "Proveedor",
    "Suministros",
    "Seguros",
    "Transporte",
    "Material de oficina",
    "Servicios profesionales",
    "Mantenimiento",
    "Publicidad",
    "Licencias de software",
    "Viajes",
    "Formación",
    "Comisiones",
]
BANK_CODES = ["2100", "0049", "0182", "0081", "2085", "0128"]
FIRST_DATE = date(2026, 9, 13)  # days >= 13: day/month order is never ambiguous


def generate(rows: int, errors: int, outliers: int, seed: int) -> list[list[str]]:
    rng = random.Random(seed)
    accounts = [
        str(IBAN.generate("ES", bank_code=rng.choice(BANK_CODES), account_code=f"{i:010d}"))
        for i in range(1, 251)
    ]
    records = []
    for n in range(1, rows + 1):
        currency = rng.choices(list(CURRENCIES), weights=CURRENCY_WEIGHTS)[0]
        # Normal around the typical amount: a skewed distribution would make the robust
        # z-score flag dozens of ordinary payments as outliers.
        amount = Decimal(max(0.2, rng.gauss(1, 0.25)) * CURRENCIES[currency])
        records.append(
            [
                f"OP-{2026_000_000 + n}",
                rng.choice(accounts),
                _es_amount(amount),
                currency,
                (FIRST_DATE + timedelta(days=rng.randrange(15))).strftime("%d/%m/%Y"),
                rng.choice(CONCEPTS),
            ]
        )
    picked = rng.sample(range(rows), outliers + errors)
    for i in picked[:outliers]:
        currency = records[i][3]
        records[i][2] = _es_amount(Decimal(CURRENCIES[currency] * rng.uniform(40, 80)))
        records[i][5] = "Compra extraordinaria"
    for k, i in enumerate(picked[outliers:]):
        _break(records, i, k % 5, rng)
    return records


def _break(records: list[list[str]], i: int, kind: int, rng: random.Random) -> None:
    if kind == 0:
        records[i][2] = "12,5,0"  # not a number
    elif kind == 1:
        records[i][3] = "XYZ"  # unsupported currency
    elif kind == 2:
        records[i][4] = "31/02/2026"  # impossible date
    elif kind == 3:
        records[i][1] = ""  # missing account
    else:
        other = rng.choice([j for j in range(len(records)) if j != i])
        records[i][0] = records[other][0]  # duplicated reference


def _es_amount(value: Decimal) -> str:
    """1234.5 -> '1.234,50'."""
    return (
        f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", "_")
        .replace(".", ",")
        .replace("_", ".")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--errors", type=int, default=0, help="rows with a blocking problem")
    parser.add_argument("--outliers", type=int, default=6, help="unusually large amounts")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("-o", "--output", type=Path, default=Path("samples/lote_5000.csv"))
    args = parser.parse_args()

    records = generate(args.rows, args.errors, args.outliers, args.seed)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(records)
    print(f"{args.output}: {args.rows} rows ({args.errors} with errors, {args.outliers} outliers)")


if __name__ == "__main__":
    main()
