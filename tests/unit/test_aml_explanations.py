from datetime import date
from decimal import Decimal

import pytest

from ledger.domain.analytics.aml import AlertSeverity, AmlAlert, AmlRule
from ledger.presentation.web.analytics_views import explain, safe_cell
from tests.factories import make_tx


@pytest.mark.parametrize(
    ("rule", "facts", "expected"),
    [
        (
            AmlRule.STRUCTURING,
            {"count": "3", "threshold": "10000", "window_days": "7"},
            "umbral de 10.000,00 EUR",
        ),
        (
            AmlRule.PASS_THROUGH,
            {"inflow": "45000", "outflow": "44500", "ratio_pct": "99", "window_days": "3"},
            "salieron 44.500,00 EUR (99 %)",
        ),
        (
            AmlRule.ROUND_AMOUNTS,
            {"count": "6", "of": "6", "share_pct": "100", "unit": "1000"},
            "múltiplos exactos de 1000 EUR",
        ),
        (AmlRule.HIGH_VELOCITY, {"count": "12", "limit": "10"}, "12 movimientos el 01/09/2026"),
        (
            AmlRule.SPIKE,
            {"month": "2026-09", "factor": "30.8", "usual": "210.35"},
            "30.8 veces su mediana mensual (210,35 EUR)",
        ),
        (
            AmlRule.REPEATED_PAYMENT,
            {"amount": "1234.56", "days_apart": "3"},
            "Pago de 1.234,56 EUR en dos lotes",
        ),
    ],
)
def test_every_rule_reads_as_a_sentence(
    rule: AmlRule, facts: dict[str, str], expected: str
) -> None:
    alert = AmlAlert(
        rule=rule,
        severity=AlertSeverity.MEDIUM,
        account="ACC",
        currency="EUR",
        transactions=(make_tx(),),
        total=Decimal("1.00"),
        first_date=date(2026, 9, 1),
        last_date=date(2026, 9, 1),
        facts=facts,
    )

    assert expected in explain(alert)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1+1", "'+1+1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("Pago proveedor", "Pago proveedor"),
        (Decimal("-12.50"), Decimal("-12.50")),  # numbers stay numbers
    ],
)
def test_csv_cells_cannot_become_formulas(value: object, expected: object) -> None:
    assert safe_cell(value) == expected
