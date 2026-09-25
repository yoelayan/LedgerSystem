"""Server-rendered UI for people: log in, upload, review, approve or reject batches.

Same use cases as the JSON API; the difference is who the actor is. Here it is always
the logged-in user, so nobody can approve "as bob" by typing his name.

Domain errors are expected outcomes of user actions (approving your own batch, a file
with unknown columns...): they are turned into a flash message and a redirect. Anything
else still propagates and becomes a logged 500.
"""

from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Final
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from pydantic import ValidationError

from ledger.application.dtos import (
    ApproveBatchCommand,
    BatchDTO,
    RegisterBatchCommand,
    RejectBatchCommand,
)
from ledger.domain.exceptions import DomainError
from ledger.domain.value_objects import (
    AnalysisReport,
    BatchStatus,
    Direction,
    DirectionSource,
    IssueCode,
    MatchMethod,
    Severity,
)
from ledger.presentation.composition import build_batch_service

MAX_UPLOAD_BYTES: Final = 5 * 1024 * 1024
PREVIEW_ROWS: Final = 200

STATUS_LABELS: Final = {
    BatchStatus.DRAFT: "Borrador",
    BatchStatus.PROCESSING: "Procesando",
    BatchStatus.PENDING_APPROVAL: "Pendiente de aprobación",
    BatchStatus.APPROVED: "Aprobado",
    BatchStatus.REJECTED: "Rechazado",
}
FIELD_LABELS: Final = {
    "external_id": "ID de transacción",
    "account": "Cuenta",
    "amount": "Importe",
    "currency": "Divisa",
    "value_date": "Fecha valor",
    "direction": "Tipo (ingreso/egreso)",
}
DIRECTION_LABELS: Final = {Direction.INFLOW: "Ingreso", Direction.OUTFLOW: "Egreso"}
DIRECTION_SOURCE_LABELS: Final = {
    DirectionSource.COLUMN: "Columna del archivo",
    DirectionSource.SIGNED_AMOUNTS: "Signo del importe (declarado al subir: negativo = egreso)",
    DirectionSource.DEFAULT: "Sin columna de tipo: todos son egresos (pagos)",
}
MATCH_METHOD_LABELS: Final = {
    MatchMethod.EXACT: "Nombre exacto",
    MatchMethod.VOCABULARY: "Nombre conocido",
    MatchMethod.SIMILARITY: "Nombre parecido (modelo)",
    MatchMethod.CONTENT: "Deducido por los valores",
}
ISSUE_LABELS: Final = {
    IssueCode.MISSING_VALUE: "Valor vacío",
    IssueCode.INVALID_AMOUNT: "Importe inválido",
    IssueCode.NON_POSITIVE_AMOUNT: "Importe no positivo",
    IssueCode.EXCESSIVE_PRECISION: "Demasiados decimales",
    IssueCode.UNSUPPORTED_CURRENCY: "Divisa no soportada",
    IssueCode.INVALID_VALUE_DATE: "Fecha inválida",
    IssueCode.DUPLICATE_EXTERNAL_ID: "ID duplicado",
    IssueCode.AMOUNT_OUTLIER: "Importe atípico",
    IssueCode.INVALID_DIRECTION: "Tipo no reconocido",
}


class UploadForm(forms.Form):
    reference = forms.CharField(label="Referencia", max_length=120)
    file = forms.FileField(label="Archivo CSV")
    signed_amounts = forms.BooleanField(
        label="Los importes llevan signo: negativo = egreso, positivo = ingreso",
        required=False,
    )

    def clean_file(self) -> UploadedFile:
        upload = self.cleaned_data["file"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("El archivo supera el máximo de 5 MB.")
        return upload


class RejectForm(forms.Form):
    reason = forms.CharField(label="Motivo", max_length=1000, widget=forms.Textarea)


@login_required
@require_GET
def batch_list(request: HttpRequest) -> HttpResponse:
    batches = build_batch_service().list_batches()
    counts = Counter(b.status for b in batches)
    selected = request.GET.get("status", "")
    if selected in BatchStatus.__members__:
        batches = [b for b in batches if b.status == selected]
    else:
        selected = ""
    return render(
        request,
        "web/batch_list.html",
        {
            "batches": batches,
            "selected": selected,
            "filters": [(s.value, STATUS_LABELS[s], counts.get(s, 0)) for s in BatchStatus],
            "total": sum(counts.values()),
            "status_labels": labels(STATUS_LABELS),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def batch_upload(request: HttpRequest) -> HttpResponse:
    form = UploadForm(request.POST, request.FILES) if request.method == "POST" else UploadForm()
    if form.is_bound and form.is_valid():
        service = build_batch_service()
        try:
            command = RegisterBatchCommand(
                reference=form.cleaned_data["reference"],
                submitted_by=request.user.get_username(),
                content=form.cleaned_data["file"].read(),
                signed_amounts=form.cleaned_data["signed_amounts"],
            )
        except ValidationError:
            # The form already checks the reference; this is e.g. a username the domain
            # does not accept as an actor.
            form.add_error(None, "No se pudo registrar el lote con estos datos.")
        else:
            try:
                batch = service.register_batch(command)
            except DomainError as exc:
                form.add_error("file", describe_error(exc))
            else:
                # Analyse straight away: uploading and then clicking "process" adds nothing.
                return _run(request, batch.id, lambda: service.process_batch(batch.id))
    return render(request, "web/batch_upload.html", {"form": form, "fields": FIELD_LABELS})


@login_required
@require_GET
def batch_detail(request: HttpRequest, batch_id: UUID) -> HttpResponse:
    try:
        batch = build_batch_service().get_batch_detail(batch_id)
    except DomainError as exc:
        messages.error(request, describe_error(exc))
        return redirect("web:batch-list")

    issues = batch.analysis.issues if batch.analysis else ()
    # Worst severity per row, to highlight the preview table.
    row_severity: dict[int, str] = {}
    for issue in issues:
        if row_severity.get(issue.row_number) != Severity.ERROR:
            row_severity[issue.row_number] = issue.severity.value
    return render(
        request,
        "web/batch_detail.html",
        {
            "batch": batch,
            "rows": [
                (row, row_severity.get(row.row_number, "")) for row in batch.rows[:PREVIEW_ROWS]
            ],
            "preview_limit": PREVIEW_ROWS,
            "hidden_rows": max(0, len(batch.rows) - PREVIEW_ROWS),
            "errors": [i for i in issues if i.severity is Severity.ERROR],
            "warnings": [i for i in issues if i.severity is Severity.WARNING],
            "status_labels": labels(STATUS_LABELS),
            "field_labels": FIELD_LABELS,
            "method_labels": labels(MATCH_METHOD_LABELS),
            "direction_labels": labels(DIRECTION_LABELS),
            "direction_source_labels": labels(DIRECTION_SOURCE_LABELS),
            "money": _money_by_currency(batch.analysis),
            "issue_labels": labels(ISSUE_LABELS),
            "is_owner": batch.created_by == request.user.get_username(),
            "can_decide": batch.status is BatchStatus.PENDING_APPROVAL,
            "reject_form": RejectForm(),
        },
    )


@login_required
@require_POST
def batch_process(request: HttpRequest, batch_id: UUID) -> HttpResponse:
    return _run(request, batch_id, lambda: build_batch_service().process_batch(batch_id))


@login_required
@require_POST
def batch_approve(request: HttpRequest, batch_id: UUID) -> HttpResponse:
    command = ApproveBatchCommand(batch_id=batch_id, approver=request.user.get_username())
    return _run(request, batch_id, lambda: build_batch_service().approve_batch(command))


@login_required
@require_POST
def batch_reject(request: HttpRequest, batch_id: UUID) -> HttpResponse:
    form = RejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indica el motivo del rechazo (máximo 1000 caracteres).")
        return redirect("web:batch-detail", batch_id=batch_id)
    command = RejectBatchCommand(
        batch_id=batch_id,
        reviewer=request.user.get_username(),
        reason=form.cleaned_data["reason"],
    )
    return _run(request, batch_id, lambda: build_batch_service().reject_batch(command))


_OUTCOME_MESSAGES: Final = {
    BatchStatus.PENDING_APPROVAL: (messages.SUCCESS, "Lote analizado: listo para aprobación."),
    BatchStatus.APPROVED: (messages.SUCCESS, "Lote aprobado."),
    BatchStatus.REJECTED: (messages.WARNING, "Lote rechazado."),
}


def _run(request: HttpRequest, batch_id: UUID, action: Callable[[], BatchDTO]) -> HttpResponse:
    try:
        batch = action()
    except DomainError as exc:
        messages.error(request, describe_error(exc))
    else:
        level, text = _OUTCOME_MESSAGES.get(batch.status, (messages.INFO, "Hecho."))
        messages.add_message(request, level, text)
    return redirect(reverse("web:batch-detail", kwargs={"batch_id": batch_id}))


_ERROR_MESSAGES: Final = {
    "SELF_APPROVAL_FORBIDDEN": "No puedes aprobar un lote que has subido tú (regla de los cuatro ojos).",
    "INVALID_STATE_TRANSITION": "Esta acción ya no es posible: el lote cambió de estado (quizá otra persona lo decidió antes).",
    "REJECTION_REASON_REQUIRED": "Indica el motivo del rechazo.",
    "BATCH_NOT_FOUND": "El lote no existe.",
    "EMPTY_BATCH": "El archivo no contiene transacciones.",
}


def describe_error(error: DomainError) -> str:
    if error.code == "MALFORMED_DATASET" and "missing_columns" in error.context:
        missing = ", ".join(FIELD_LABELS.get(f, f) for f in error.context["missing_columns"])
        found = ", ".join(error.context.get("available_columns", [])) or "ninguna"
        return (
            f"No se pudo identificar qué columna contiene: {missing}. "
            f"Columnas encontradas en el archivo: {found}."
        )
    return _ERROR_MESSAGES.get(error.code, error.message)


def _money_by_currency(
    report: AnalysisReport | None,
) -> list[tuple[str, Decimal, Decimal, Decimal]]:
    """(currency, inflows, outflows, net) for the summary table."""
    if report is None:
        return []
    zero = Decimal("0.00")
    return [
        (
            code,
            report.inflow_by_currency.get(code, zero),
            report.outflow_by_currency.get(code, zero),
            report.inflow_by_currency.get(code, zero) - report.outflow_by_currency.get(code, zero),
        )
        for code in sorted(report.totals_by_currency)
    ]


def labels[K](mapping: dict[K, str]) -> dict[str, str]:
    """Templates look labels up by the enum's string value."""
    return {str(key): value for key, value in mapping.items()}
