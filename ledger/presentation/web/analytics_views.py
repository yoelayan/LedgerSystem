"""Analytics screens: overview and trends, timeline, AML alerts, ERP reconciliation.

Every screen reads the same filters (dates, currency, account, whether to include
batches still pending approval) and can be downloaded as CSV for reporting.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any, Final

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from ledger.application.analytics import ReconciliationDTO
from ledger.application.dtos import ReconcileCommand, TransactionQuery
from ledger.domain.analytics.aml import AlertSeverity, AmlAlert, AmlRule
from ledger.domain.analytics.reconciliation import Difference, MatchKind
from ledger.domain.analytics.timeline import BatchEventKind
from ledger.domain.analytics.trends import Granularity
from ledger.domain.exceptions import DomainError
from ledger.domain.value_objects import SUPPORTED_CURRENCIES
from ledger.presentation.composition import build_analytics_service
from ledger.presentation.templatetags.ledger_ui import format_money
from ledger.presentation.web.views import (
    DIRECTION_LABELS,
    MATCH_METHOD_LABELS,
    MAX_UPLOAD_BYTES,
    describe_error,
    labels,
)

CHRONOLOGY_LIMIT: Final = 90

GRANULARITY_LABELS: Final = {
    Granularity.DAY: "Día",
    Granularity.WEEK: "Semana",
    Granularity.MONTH: "Mes",
}
RULE_LABELS: Final = {
    AmlRule.STRUCTURING: "Fraccionamiento (pitufeo)",
    AmlRule.PASS_THROUGH: "Cuenta de paso",
    AmlRule.ROUND_AMOUNTS: "Importes redondos",
    AmlRule.HIGH_VELOCITY: "Muchos movimientos en un día",
    AmlRule.SPIKE: "Pico de actividad",
    AmlRule.REPEATED_PAYMENT: "Pago repetido entre lotes",
}
RULE_DESCRIPTIONS: Final = {
    AmlRule.STRUCTURING: (
        "Varios importes justo por debajo del umbral de declaración en pocos días: "
        "una cantidad grande troceada para no llamar la atención."
    ),
    AmlRule.PASS_THROUGH: (
        "Entra dinero y casi todo vuelve a salir en pocos días: la cuenta hace de puente."
    ),
    AmlRule.ROUND_AMOUNTS: (
        "La mayoría de los movimientos son cifras redondas grandes, poco habituales en "
        "operaciones comerciales reales."
    ),
    AmlRule.HIGH_VELOCITY: "Un número inusual de movimientos de una cuenta en un solo día.",
    AmlRule.SPIKE: "El volumen de un mes se dispara frente al historial de la propia cuenta.",
    AmlRule.REPEATED_PAYMENT: (
        "El mismo pago (cuenta e importe) aparece en lotes distintos con pocos días de "
        "diferencia: posible pago duplicado."
    ),
}
SEVERITY_LABELS: Final = {
    AlertSeverity.HIGH: "Alta",
    AlertSeverity.MEDIUM: "Media",
    AlertSeverity.LOW: "Baja",
}
EVENT_LABELS: Final = {
    BatchEventKind.UPLOADED: "subió",
    BatchEventKind.APPROVED: "aprobó",
    BatchEventKind.REJECTED: "rechazó",
}
MATCH_KIND_LABELS: Final = {
    MatchKind.REFERENCE: "Por referencia",
    MatchKind.AMOUNT_AND_DATE: "Por importe y fecha",
}
DIFFERENCE_LABELS: Final = {
    Difference.AMOUNT: "importe",
    Difference.CURRENCY: "divisa",
    Difference.DIRECTION: "ingreso/egreso",
    Difference.DATE: "fecha",
}


class FilterForm(forms.Form):
    date_from = forms.DateField(label="Desde", required=False)
    date_to = forms.DateField(label="Hasta", required=False)
    currency = forms.ChoiceField(label="Divisa", required=False)
    account = forms.CharField(label="Cuenta", required=False, max_length=255)
    include_pending = forms.BooleanField(label="Incluir pendientes de aprobación", required=False)
    granularity = forms.ChoiceField(
        label="Agrupar por",
        required=False,
        choices=[(g.value, label) for g, label in GRANULARITY_LABELS.items()],
    )

    def __init__(self, data: Any, currencies: Sequence[str]) -> None:
        super().__init__(data)
        self.fields["currency"].choices = [("", "Todas"), *((c, c) for c in currencies)]

    def query(self) -> TransactionQuery:
        data = self.cleaned_data if self.is_valid() else {}
        return TransactionQuery(
            date_from=data.get("date_from"),
            date_to=data.get("date_to"),
            currency=data.get("currency") or None,
            account=(data.get("account") or "").strip() or None,
            include_pending=bool(data.get("include_pending")),
        )

    def selected_granularity(self) -> Granularity:
        value = self.cleaned_data.get("granularity") if self.is_valid() else None
        return Granularity(value) if value else Granularity.MONTH


class ReconcileForm(forms.Form):
    file = forms.FileField(label="Exportación del libro contable (CSV)")
    default_currency = forms.ChoiceField(
        label="Divisa si el archivo no la indica",
        choices=[(c, c) for c in sorted(SUPPORTED_CURRENCIES)],
        initial="EUR",
    )
    invert_debit_credit = forms.BooleanField(
        label="Invertir Debe/Haber (el archivo es de proveedores o clientes, no de bancos)",
        required=False,
    )
    date_tolerance_days = forms.IntegerField(
        label="Tolerancia de fechas (días)", min_value=0, max_value=31, initial=3
    )

    def clean_file(self) -> Any:
        upload = self.cleaned_data["file"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("El archivo supera el máximo de 5 MB.")
        return upload


@login_required
@require_GET
def overview(request: HttpRequest) -> HttpResponse:
    service = build_analytics_service()
    form = FilterForm(request.GET, service.currencies())
    result = service.overview(form.query(), form.selected_granularity())
    if request.GET.get("format") == "csv":
        return _csv(
            "analitica.csv",
            ["periodo", "divisa", "ingresos", "egresos", "neto", "movimientos"],
            (
                [
                    p.period,
                    p.currency,
                    p.totals.inflow,
                    p.totals.outflow,
                    p.totals.net,
                    p.totals.count,
                ]
                for p in result.series
            ),
        )
    charts = [
        {
            "currency": currency,
            "element_id": f"{currency}-flow-data",
            "labels": [p.period.isoformat() for p in result.series if p.currency == currency],
            "inflow": [str(p.totals.inflow) for p in result.series if p.currency == currency],
            "outflow": [str(p.totals.outflow) for p in result.series if p.currency == currency],
            "net": [str(p.totals.net) for p in result.series if p.currency == currency],
        }
        for currency in result.summary
    ]
    return render(
        request,
        "web/analytics/overview.html",
        {
            "form": form,
            "result": result,
            "charts": charts,
            "granularity_label": GRANULARITY_LABELS[result.granularity],
            "section": "overview",
        },
    )


@login_required
@require_GET
def timeline(request: HttpRequest) -> HttpResponse:
    service = build_analytics_service()
    form = FilterForm(request.GET, service.currencies())
    result = service.timeline(form.query())
    if request.GET.get("format") == "csv":
        return _csv(
            "linea_de_tiempo.csv",
            ["fecha", "divisa", "ingresos", "egresos", "neto", "saldo_acumulado", "movimientos"],
            (
                [
                    d.day,
                    d.currency,
                    d.totals.inflow,
                    d.totals.outflow,
                    d.totals.net,
                    d.balance,
                    d.totals.count,
                ]
                for d in result.days
            ),
        )
    currencies = sorted({d.currency for d in result.days})
    charts = [
        {
            "currency": currency,
            "element_id": f"{currency}-balance-data",
            "labels": [d.day.isoformat() for d in result.days if d.currency == currency],
            "balance": [str(d.balance) for d in result.days if d.currency == currency],
        }
        for currency in currencies
    ]
    return render(
        request,
        "web/analytics/timeline.html",
        {
            "form": form,
            "result": result,
            "charts": charts,
            "entries": result.entries[:CHRONOLOGY_LIMIT],
            "hidden_entries": max(0, len(result.entries) - CHRONOLOGY_LIMIT),
            "event_labels": labels(EVENT_LABELS),
            "section": "timeline",
        },
    )


@login_required
@require_GET
def aml(request: HttpRequest) -> HttpResponse:
    service = build_analytics_service()
    form = FilterForm(request.GET, service.currencies())
    result = service.aml_alerts(form.query())
    if request.GET.get("format") == "csv":
        return _csv(
            "alertas_blanqueo.csv",
            [
                "severidad",
                "regla",
                "cuenta",
                "divisa",
                "total",
                "desde",
                "hasta",
                "movimientos",
                "detalle",
            ],
            (
                [
                    SEVERITY_LABELS[a.severity],
                    RULE_LABELS[a.rule],
                    a.account,
                    a.currency,
                    a.total,
                    a.first_date,
                    a.last_date,
                    len(a.transactions),
                    explain(a),
                ]
                for a in result.alerts
            ),
        )
    counts = dict.fromkeys(AmlRule, 0)
    for alert in result.alerts:
        counts[alert.rule] += 1
    return render(
        request,
        "web/analytics/aml.html",
        {
            "form": form,
            "result": result,
            "alerts": [(a, explain(a)) for a in result.alerts],
            "rules": [
                (rule, RULE_LABELS[rule], RULE_DESCRIPTIONS[rule], counts[rule]) for rule in AmlRule
            ],
            "thresholds": sorted(result.policy.reporting_thresholds.items()),
            "rule_labels": labels(RULE_LABELS),
            "severity_labels": labels(SEVERITY_LABELS),
            "direction_labels": labels(DIRECTION_LABELS),
            "section": "aml",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def reconciliation(request: HttpRequest) -> HttpResponse:
    form = (
        ReconcileForm(request.POST, request.FILES) if request.method == "POST" else ReconcileForm()
    )
    result: ReconciliationDTO | None = None
    if form.is_bound and form.is_valid():
        try:
            result = build_analytics_service().reconcile(
                ReconcileCommand(
                    content=form.cleaned_data["file"].read(),
                    default_currency=form.cleaned_data["default_currency"],
                    invert_debit_credit=form.cleaned_data["invert_debit_credit"],
                    date_tolerance_days=form.cleaned_data["date_tolerance_days"],
                )
            )
        except DomainError as exc:
            form.add_error("file", describe_error(exc))
    return render(
        request,
        "web/analytics/reconciliation.html",
        {
            "form": form,
            "result": result,
            "report_csv": _reconciliation_csv(result) if result else "",
            "discrepancies": [
                (d, ", ".join(DIFFERENCE_LABELS[x] for x in d.differences))
                for d in (result.result.discrepancies if result else ())
            ],
            "match_kind_labels": labels(MATCH_KIND_LABELS),
            "method_labels": labels(MATCH_METHOD_LABELS),
            "direction_labels": labels(DIRECTION_LABELS),
            "field_labels": RECONCILIATION_FIELD_LABELS,
            "section": "reconciliation",
        },
    )


RECONCILIATION_FIELD_LABELS: Final = {
    "external_id": "Referencia",
    "value_date": "Fecha",
    "amount": "Importe",
    "debit": "Debe",
    "credit": "Haber",
    "currency": "Divisa",
    "direction": "Debe/Haber",
    "description": "Concepto",
}


def explain(alert: AmlAlert) -> str:
    """The alert's facts as a sentence a reviewer can check."""
    cur = alert.currency
    f = {
        key: format_money(value) if key in _MONEY_FACTS else value
        for key, value in alert.facts.items()
    }
    total = format_money(alert.total)
    if alert.rule is AmlRule.STRUCTURING:
        return (
            f"{f['count']} movimientos justo por debajo del umbral de {f['threshold']} {cur} "
            f"en {f['window_days']} días; suman {total} {cur}."
        )
    if alert.rule is AmlRule.PASS_THROUGH:
        return (
            f"Entraron {f['inflow']} {cur} y salieron {f['outflow']} {cur} "
            f"({f['ratio_pct']} %) en {f['window_days']} días."
        )
    if alert.rule is AmlRule.ROUND_AMOUNTS:
        return (
            f"{f['count']} de {f['of']} movimientos ({f['share_pct']} %) son múltiplos exactos "
            f"de {f['unit']} {cur}."
        )
    if alert.rule is AmlRule.HIGH_VELOCITY:
        return f"{f['count']} movimientos el {alert.first_date:%d/%m/%Y} (umbral: {f['limit']})."
    if alert.rule is AmlRule.SPIKE:
        return (
            f"En {f['month']} movió {total} {cur}: {f['factor']} veces su mediana "
            f"mensual ({f['usual']} {cur})."
        )
    return (
        f"Pago de {f['amount']} {cur} en dos lotes distintos, con {f['days_apart']} días "
        "de diferencia."
    )


_MONEY_FACTS: Final = frozenset({"threshold", "inflow", "outflow", "usual", "amount"})
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralise CSV/formula injection: text from uploaded files ("=HYPERLINK(...)")
    must not run as a formula when the report is opened in a spreadsheet.

    Only text is touched; numbers (Decimal, int, dates) are written as they are.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _csv(filename: str, header: list[str], rows: Iterable[list[Any]]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # BOM + semicolons: opens correctly in Excel with a Spanish locale.
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(header)
    writer.writerows([safe_cell(cell) for cell in row] for row in rows)
    return response


class _SafeWriter:
    """csv.writer that neutralises formula injection in every cell (see `safe_cell`)."""

    def __init__(self, writer: Any) -> None:
        self._writer = writer

    def writerow(self, row: Iterable[Any]) -> None:
        self._writer.writerow([safe_cell(cell) for cell in row])


def _reconciliation_csv(result: ReconciliationDTO) -> str:
    buffer = io.StringIO()
    writer = _SafeWriter(csv.writer(buffer, delimiter=";"))
    writer.writerow(
        ["resultado", "referencia_ledger", "referencia_erp", "importe", "divisa", "sentido",
         "fecha_ledger", "fecha_erp", "detalle"]
    )  # fmt: skip
    r = result.result
    for m in r.matches:
        writer.writerow(
            ["conciliado", m.transaction.external_id, m.entry.reference, m.entry.amount,
             m.entry.currency, m.entry.direction, m.transaction.value_date, m.entry.entry_date,
             MATCH_KIND_LABELS[m.kind]]
        )  # fmt: skip
    for d in r.discrepancies:
        writer.writerow(
            ["discrepancia", d.transaction.external_id, d.entry.reference,
             f"{d.transaction.amount} / {d.entry.amount}", d.entry.currency, d.entry.direction,
             d.transaction.value_date, d.entry.entry_date,
             ", ".join(DIFFERENCE_LABELS[x] for x in d.differences)]
        )  # fmt: skip
    for t in r.only_in_ledgersystem:
        writer.writerow(
            ["solo_ledgersystem", t.external_id, "", t.amount, t.currency, t.direction,
             t.value_date, "", ""]
        )  # fmt: skip
    for e in r.only_in_erp:
        writer.writerow(
            ["solo_erp", "", e.reference, e.amount, e.currency, e.direction, "", e.entry_date,
             e.description]
        )  # fmt: skip
    return "\ufeff" + buffer.getvalue()
