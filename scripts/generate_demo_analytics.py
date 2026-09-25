"""Generate six months of batches and an ERP ledger export to try the analytics module.

Writes to samples/analitica/:

* lote_2026_04.csv ... lote_2026_09.csv - one batch per month, Spanish bank format, with a
  "Tipo" column (Ingreso/Egreso): supplier payments, payroll and customer collections.
* erp_mayor_bancos_2026_09.csv - the September entries of the bank account (572) as an
  ERP exports them: Debe/Haber columns, booked 0-2 days after the value date.

Planted on purpose, so every screen has something to show:

* AML patterns, one account each: structuring (4 inflows just below 10,000 EUR in 5
  days), pass-through (45,000 in, 44,500 out within 2 days), round amounts, a September
  spike on a quiet account, 12 payments on one day, and a payment repeated in the
  August and September batches.
* Reconciliation differences in the ERP file: one amount that does not match, two
  approved payments never booked, two booked entries without a batch (bank fee,
  unknown transfer), entries without reference (matched by amount and date) and one
  unreadable line.

Deterministic (fixed seed). Load it with `python manage.py load_demo_analytics`.
"""

import csv
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from schwifty import IBAN

OUT = Path(__file__).resolve().parent.parent / "samples" / "analitica"
MONTHS = [date(2026, m, 1) for m in range(4, 10)]
BATCH_HEADER = ["Referencia", "Cuenta", "Importe", "Moneda", "Fecha valor", "Tipo", "Concepto"]
ERP_HEADER = ["Fecha asiento", "Documento", "Concepto", "Debe", "Haber"]


@dataclass
class Movement:
    reference: str
    account: str
    amount: Decimal
    value_date: date
    inflow: bool
    concept: str
    currency: str = "EUR"
    extra: dict[str, str] = field(default_factory=dict)


def es_amount(value: Decimal) -> str:
    text = f"{value.quantize(Decimal('0.01')):,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def iban(rng: random.Random, n: int) -> str:
    bank = rng.choice(["2100", "0049", "0182", "0081", "2085"])
    return str(IBAN.generate("ES", bank_code=bank, account_code=f"{n:010d}"))


def generate(seed: int = 7) -> dict[date, list[Movement]]:
    rng = random.Random(seed)
    suppliers = [iban(rng, 1000 + i) for i in range(40)]
    employees = [iban(rng, 2000 + i) for i in range(25)]
    customers = [iban(rng, 3000 + i) for i in range(30)]
    counter = iter(range(1, 100_000))

    def ref(month: date) -> str:
        return f"OP-{month:%Y%m}-{next(counter):05d}"

    def day(month: date, lo: int = 1, hi: int = 28) -> date:
        return month.replace(day=rng.randint(lo, hi))

    batches: dict[date, list[Movement]] = {}
    for index, month in enumerate(MONTHS):
        growth = Decimal(1 + index * 0.04)  # business grows ~4 % a month
        rows: list[Movement] = []
        for account in employees:
            salary = Decimal(rng.randint(1800, 3200)) + Decimal(rng.randint(0, 99)) / 100
            rows.append(
                Movement(ref(month), account, salary, month.replace(day=27), False, "Nómina")
            )
        for _ in range(140):
            amount = Decimal(rng.gauss(900, 250) * float(growth)).quantize(Decimal("0.01"))
            rows.append(
                Movement(
                    ref(month),
                    rng.choice(suppliers),
                    max(amount, Decimal("35.10")),
                    day(month),
                    False,
                    "Proveedor",
                )
            )
        for _ in range(90):
            amount = Decimal(rng.gauss(1900, 500) * float(growth)).quantize(Decimal("0.01"))
            rows.append(
                Movement(
                    ref(month),
                    rng.choice(customers),
                    max(amount, Decimal("80.40")),
                    day(month),
                    True,
                    "Cobro cliente",
                )
            )
        for _ in range(12):  # a few USD customers
            amount = Decimal(rng.gauss(2500, 400)).quantize(Decimal("0.01"))
            rows.append(
                Movement(
                    ref(month),
                    rng.choice(customers),
                    amount,
                    day(month),
                    True,
                    "Cobro exportación",
                    "USD",
                )
            )
        batches[month] = rows

    plant(batches, rng, ref)
    for rows in batches.values():
        rows.sort(key=lambda m: (m.value_date, m.reference))
    return batches


def plant(batches: dict[date, list[Movement]], rng: random.Random, ref) -> None:
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    structuring = iban(rng, 9001)
    for i, amount in enumerate(["9400.00", "9850.00", "9600.00", "9900.00"]):
        batches[aug].append(
            Movement(
                ref(aug),
                structuring,
                Decimal(amount),
                date(2026, 8, 11 + i),
                True,
                "Ingreso en efectivo",
            )
        )
    pass_through = iban(rng, 9002)
    batches[sep] += [
        Movement(
            ref(sep),
            pass_through,
            Decimal("45000.00"),
            date(2026, 9, 10),
            True,
            "Transferencia recibida",
        ),
        Movement(
            ref(sep),
            pass_through,
            Decimal("20000.00"),
            date(2026, 9, 11),
            False,
            "Transferencia a tercero",
        ),
        Movement(
            ref(sep),
            pass_through,
            Decimal("24500.00"),
            date(2026, 9, 12),
            False,
            "Transferencia a tercero",
        ),
    ]
    rounded = iban(rng, 9003)
    for month, amount in zip(
        batches, ["5000", "8000", "3000", "6000", "4000", "7000"], strict=True
    ):
        batches[month].append(
            Movement(ref(month), rounded, Decimal(amount), month.replace(day=15), False, "Asesoría")
        )
    quiet = iban(rng, 9004)
    for month in list(batches)[:-1]:
        batches[month].append(
            Movement(
                ref(month), quiet, Decimal("210.35"), month.replace(day=5), False, "Mantenimiento"
            )
        )
    batches[sep].append(
        Movement(
            ref(sep),
            quiet,
            Decimal("6480.90"),
            date(2026, 9, 5),
            False,
            "Mantenimiento extraordinario",
        )
    )
    busy = iban(rng, 9005)
    for i in range(12):
        batches[sep].append(
            Movement(
                ref(sep),
                busy,
                Decimal(f"{310 + i * 7}.25"),
                date(2026, 9, 18),
                False,
                "Pago fraccionado",
            )
        )
    duplicate = iban(rng, 9006)
    batches[aug].append(
        Movement(
            ref(aug), duplicate, Decimal("1234.56"), date(2026, 8, 30), False, "Factura 2026-0831"
        )
    )
    batches[sep].append(
        Movement(
            ref(sep), duplicate, Decimal("1234.56"), date(2026, 9, 2), False, "Factura 2026-0831"
        )
    )


def write_batches(batches: dict[date, list[Movement]]) -> None:
    for month, rows in batches.items():
        with (OUT / f"lote_{month:%Y_%m}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";", lineterminator="\n")
            writer.writerow(BATCH_HEADER)
            for m in rows:
                writer.writerow(
                    [m.reference, m.account, es_amount(m.amount), m.currency,
                     m.value_date.strftime("%d/%m/%Y"), "Ingreso" if m.inflow else "Egreso", m.concept]
                )  # fmt: skip


def write_erp(september: list[Movement], seed: int = 11) -> None:
    rng = random.Random(seed)
    eur = [m for m in september if m.currency == "EUR"]
    never_booked = {m.reference for m in rng.sample(eur, 2)}
    wrong_amount = rng.choice([m for m in eur if m.reference not in never_booked]).reference
    lines: list[tuple[date, str, str, Decimal, bool]] = []
    for m in eur:
        if m.reference in never_booked:
            continue
        amount = m.amount + Decimal("10.00") if m.reference == wrong_amount else m.amount
        reference = "" if rng.random() < 0.1 else m.reference  # some entries lost their reference
        lines.append(
            (
                m.value_date + timedelta(days=rng.randint(0, 2)),
                reference,
                m.concept,
                amount,
                m.inflow,
            )
        )
    lines += [
        (date(2026, 9, 30), "", "Comisión mantenimiento cuenta", Decimal("15.00"), False),
        (
            date(2026, 9, 22),
            "TRF-889120",
            "Transferencia sin justificar",
            Decimal("2500.00"),
            False,
        ),
    ]
    lines.sort(key=lambda line: (line[0], line[1]))
    with (OUT / "erp_mayor_bancos_2026_09.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";", lineterminator="\n")
        writer.writerow(ERP_HEADER)
        for booked, reference, concept, amount, inflow in lines:
            debit, credit = (es_amount(amount), "") if inflow else ("", es_amount(amount))
            writer.writerow([booked.strftime("%d/%m/%Y"), reference, concept, debit, credit])
        writer.writerow(["30/09/2026", "AJ-1", "Ajuste mal registrado", "100,00", "100,00"])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    batches = generate()
    write_batches(batches)
    write_erp(batches[date(2026, 9, 1)])
    total = sum(len(rows) for rows in batches.values())
    print(f"{OUT}: {len(batches)} batches ({total} movements) + ERP ledger for 2026-09")


if __name__ == "__main__":
    main()
