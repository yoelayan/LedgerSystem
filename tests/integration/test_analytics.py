"""Analytics end to end: projection of batches, read model, web screens, API, demo data."""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client
from django.test.utils import override_settings

from ledger.application.dtos import ApproveBatchCommand, RegisterBatchCommand, TransactionQuery
from ledger.application.services import BatchService
from ledger.infrastructure.models import TransactionModel
from ledger.infrastructure.read_models import DjangoTransactionReadModel
from tests.factories import CLEAN_CSV, DIRTY_CSV, SUBMITTER

pytestmark = pytest.mark.django_db

LEDGER = (
    "Fecha asiento;Documento;Concepto;Debe;Haber\n"
    "01/09/2026;TX-1;Pago;;100,00\n"
    "02/09/2026;TX-2;Pago;;999,00\n"  # booked with a different amount
    "30/09/2026;;Comisión;;15,00\n"
).encode()
FLOWS = (
    b"Referencia,Cuenta,Importe,Moneda,Fecha,Tipo\n"
    b"IN-1,ACC-9,500.00,EUR,2026-09-01,Ingreso\n"
    b"OUT-1,ACC-9,200.00,EUR,2026-09-02,Egreso\n"
)


def _approved(service: BatchService, content: bytes = CLEAN_CSV, reference: str = "sept") -> Any:
    batch = service.register_batch(
        RegisterBatchCommand(reference=reference, submitted_by=SUBMITTER, content=content)
    )
    service.process_batch(batch.id)
    return service.approve_batch(ApproveBatchCommand(batch_id=batch.id, approver="bob"))


@pytest.fixture
def bob() -> Client:
    User.objects.create_user(username="bob", password="pw-12345")
    client = Client()
    client.login(username="bob", password="pw-12345")
    return client


class TestProjection:
    def test_only_valid_rows_are_projected_once(self, service: BatchService) -> None:
        batch = service.register_batch(
            RegisterBatchCommand(reference="d", submitted_by=SUBMITTER, content=DIRTY_CSV)
        )
        assert not TransactionModel.objects.exists()  # not analysed yet

        service.process_batch(batch.id)

        assert list(TransactionModel.objects.values_list("external_id", flat=True)) == ["TX-1"]

    def test_approving_does_not_duplicate(self, service: BatchService) -> None:
        _approved(service)

        assert TransactionModel.objects.count() == 3


class TestReadModel:
    def test_filters_and_statuses(self, service: BatchService) -> None:
        _approved(service)
        pending = service.register_batch(
            RegisterBatchCommand(reference="p", submitted_by=SUBMITTER, content=FLOWS)
        )
        service.process_batch(pending.id)
        read = DjangoTransactionReadModel()

        assert len(read.transactions(TransactionQuery())) == 3
        assert len(read.transactions(TransactionQuery(include_pending=True))) == 5
        assert [t.external_id for t in read.transactions(TransactionQuery(currency="EUR"))] == [
            "TX-3"
        ]
        by_account = read.transactions(TransactionQuery(account="acc-9", include_pending=True))
        assert [(t.external_id, t.direction) for t in by_account] == [
            ("IN-1", "INFLOW"),
            ("OUT-1", "OUTFLOW"),
        ]
        dated = TransactionQuery(date_from=date(2026, 9, 2), date_to=date(2026, 9, 2))
        assert [t.external_id for t in read.transactions(dated)] == ["TX-2"]
        assert read.currencies() == ["EUR", "USD"]

    def test_batch_events(self, service: BatchService) -> None:
        _approved(service)
        rejected = service.register_batch(
            RegisterBatchCommand(reference="bad", submitted_by=SUBMITTER, content=DIRTY_CSV)
        )
        service.process_batch(rejected.id)
        read = DjangoTransactionReadModel()

        kinds = [(e.reference, e.kind) for e in read.batch_events(TransactionQuery())]
        assert kinds == [("sept", "UPLOADED"), ("sept", "APPROVED")]
        # Filtering by account keeps only the batches that touched it.
        assert read.batch_events(TransactionQuery(account="NOPE")) == []
        assert read.batch_events(TransactionQuery(date_to=date(2000, 1, 1))) == []


class TestWeb:
    def test_screens_require_login(self, client: Client) -> None:
        for path in [
            "/analytics/",
            "/analytics/timeline/",
            "/analytics/aml/",
            "/analytics/reconciliation/",
        ]:
            assert client.get(path).status_code == 302

    def test_overview(self, bob: Client, service: BatchService) -> None:
        _approved(service)

        response = bob.get("/analytics/?granularity=DAY")

        assert response.context["result"].transaction_count == 3
        assert [c["currency"] for c in response.context["charts"]] == ["EUR", "USD"]
        assert "250,50" in response.content.decode()  # Spanish money format

    def test_overview_empty_and_invalid_filters(self, bob: Client) -> None:
        response = bob.get("/analytics/?date_from=not-a-date")

        assert response.context["result"].transaction_count == 0
        assert "Revisa los filtros" in response.content.decode()

    @pytest.mark.parametrize(
        ("path", "header"),
        [
            ("/analytics/?format=csv", "periodo;divisa"),
            ("/analytics/timeline/?format=csv", "fecha;divisa"),
            ("/analytics/aml/?format=csv", "severidad;regla"),
        ],
    )
    def test_csv_downloads(
        self, bob: Client, service: BatchService, path: str, header: str
    ) -> None:
        _approved(service)

        response = bob.get(path)

        assert response["Content-Type"].startswith("text/csv")
        assert response.content.decode("utf-8-sig").startswith(header)

    def test_timeline(self, bob: Client, service: BatchService) -> None:
        _approved(service)

        response = bob.get("/analytics/timeline/?include_pending=on")

        assert response.context["entries"]
        assert "aprobó" in response.content.decode()

    def test_aml(self, bob: Client, service: BatchService) -> None:
        structuring = (
            b"Referencia,Cuenta,Importe,Moneda,Fecha\n"
            b"S-1,ACC-S,9500.00,EUR,2026-09-01\n"
            b"S-2,ACC-S,9800.00,EUR,2026-09-02\n"
            b"S-3,ACC-S,9100.00,EUR,2026-09-03\n"
        )
        _approved(service, structuring)

        response = bob.get("/analytics/aml/")

        [(alert, explanation)] = response.context["alerts"]
        assert alert.rule == "STRUCTURING"
        assert explanation.startswith("3 movimientos justo por debajo del umbral de 10.000,00 EUR")

    def test_reconciliation(self, bob: Client, service: BatchService) -> None:
        _approved(service, CLEAN_CSV.replace(b"USD", b"EUR"))

        response = bob.post(
            "/analytics/reconciliation/",
            {
                "file": SimpleUploadedFile("mayor.csv", LEDGER, "text/csv"),
                "default_currency": "EUR",
                "date_tolerance_days": "3",
            },
        )

        result = response.context["result"].result
        assert [m.transaction.external_id for m in result.matches] == ["TX-1"]
        assert [
            d.transaction.external_id for d in response.context["result"].result.discrepancies
        ] == ["TX-2"]
        assert [t.external_id for t in result.only_in_ledgersystem] == ["TX-3"]
        assert [e.amount for e in result.only_in_erp] == [Decimal("15.00")]
        assert response.context["discrepancies"][0][1] == "importe"
        assert "solo_erp" in response.context["report_csv"]

    def test_reconciliation_form_errors(self, bob: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        from ledger.presentation.web import analytics_views

        assert bob.get("/analytics/reconciliation/").context["result"] is None
        bad = bob.post(
            "/analytics/reconciliation/",
            {
                "file": SimpleUploadedFile("x.csv", b"foo,bar\n1,2\n"),
                "default_currency": "EUR",
                "date_tolerance_days": "3",
            },
        )
        assert "No se pudo identificar" in bad.context["form"].errors["file"][0]

        monkeypatch.setattr(analytics_views, "MAX_UPLOAD_BYTES", 5)
        big = bob.post(
            "/analytics/reconciliation/",
            {
                "file": SimpleUploadedFile("x.csv", LEDGER),
                "default_currency": "EUR",
                "date_tolerance_days": "3",
            },
        )
        assert "5 MB" in big.context["form"].errors["file"][0]

    def test_chart_library_is_served_locally(self, client: Client) -> None:
        response = client.get("/assets/chart-4.4.4.umd.js")

        assert response["Content-Type"] == "application/javascript"
        assert "immutable" in response["Cache-Control"]


class TestApi:
    BASE = "/api/v1/analytics"

    def test_overview_timeline_aml(self, client: Client, service: BatchService) -> None:
        _approved(service)

        overview = client.get(f"{self.BASE}/overview/?granularity=week&currency=USD").json()
        timeline = client.get(f"{self.BASE}/timeline/").json()
        aml = client.get(f"{self.BASE}/aml/?include_pending=true").json()

        assert overview["transaction_count"] == 2
        assert overview["granularity"] == "WEEK"
        assert len(timeline["days"]) == 3
        assert aml["alerts"] == []

    def test_invalid_parameters_are_400(self, client: Client) -> None:
        assert client.get(f"{self.BASE}/overview/?granularity=YEAR").status_code == 400
        assert client.get(f"{self.BASE}/aml/?date_from=yesterday").status_code == 400

    def test_reconciliation(self, client: Client, service: BatchService) -> None:
        _approved(service, CLEAN_CSV.replace(b"USD", b"EUR"))

        response = client.post(
            f"{self.BASE}/reconciliation/",
            {"file": SimpleUploadedFile("mayor.csv", LEDGER), "date_tolerance_days": "3"},
        )

        body = json.loads(response.content)
        assert len(body["result"]["matches"]) == 1
        assert body["period_from"] == "2026-08-29"

    def test_reconciliation_needs_a_file(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ledger.presentation.api import analytics_views

        assert client.post(f"{self.BASE}/reconciliation/").status_code == 400
        monkeypatch.setattr(analytics_views, "MAX_UPLOAD_BYTES", 5)
        too_big = client.post(
            f"{self.BASE}/reconciliation/", {"file": SimpleUploadedFile("x.csv", LEDGER)}
        )
        assert too_big.status_code == 400


class TestDemoData:
    def test_loads_six_approved_months_once(self) -> None:
        call_command("load_demo_analytics")
        call_command("load_demo_analytics")

        read = DjangoTransactionReadModel()
        transactions = read.transactions(TransactionQuery())
        assert len({t.value_date.month for t in transactions}) == 6
        assert len(read.batch_events(TransactionQuery())) == 12

    @override_settings(ENVIRONMENT="production")
    def test_refuses_in_production(self) -> None:
        with pytest.raises(CommandError):
            call_command("load_demo_analytics")

    def test_needs_the_sample_files(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from ledger.presentation.management.commands import load_demo_analytics

        monkeypatch.setattr(load_demo_analytics, "SAMPLES", tmp_path)
        with pytest.raises(CommandError, match="No demo batches"):
            call_command("load_demo_analytics")
