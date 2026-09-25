"""The server-rendered UI: login, upload, review and decide, as real users."""

from typing import Any
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import override_settings

from ledger.application.dtos import RegisterBatchCommand
from ledger.application.services import BatchService
from ledger.presentation.web import views
from tests.factories import CLEAN_CSV, DIRTY_CSV

pytestmark = pytest.mark.django_db

PASSWORD = "s3cret-pass"
ES_CSV = (
    "Referencia;Nº de cuenta;Importe;Moneda;Fecha valor\n"
    "OP-1;ES9121000418450200051332;12.50;EUR;2026-09-01\n"
).encode()


@pytest.fixture
def alice() -> Client:
    return _client_for("alice")


@pytest.fixture
def bob() -> Client:
    return _client_for("bob")


def _client_for(username: str) -> Client:
    User.objects.create_user(username=username, password=PASSWORD)
    client = Client()
    assert client.login(username=username, password=PASSWORD)
    return client


def _upload(client: Client, content: bytes = CLEAN_CSV, reference: str = "sept") -> Any:
    return client.post(
        "/batches/new/",
        {"reference": reference, "file": SimpleUploadedFile("b.csv", content, "text/csv")},
        follow=True,
    )


def _batch_url(response: Any) -> str:
    url: str = response.redirect_chain[-1][0]
    return url


def _messages(response: Any) -> list[str]:
    return [str(m) for m in response.context["messages"]]


class TestAuthentication:
    @pytest.mark.parametrize("path", ["/", "/batches/new/", f"/batches/{uuid4()}/"])
    def test_pages_require_login(self, client: Client, path: str) -> None:
        response = client.get(path)

        assert response.status_code == 302
        assert response["Location"].startswith("/login/")

    def test_login_page_lists_demo_users_outside_production(self, client: Client) -> None:
        assert b"alice" in client.get("/login/").content

    def test_login_and_logout(self, client: Client) -> None:
        User.objects.create_user(username="carol", password=PASSWORD)

        response = client.post("/login/", {"username": "carol", "password": PASSWORD})
        assert response["Location"] == "/"
        assert client.get("/").status_code == 200

        client.post("/logout/")
        assert client.get("/").status_code == 302

    def test_wrong_password(self, client: Client) -> None:
        response = client.post("/login/", {"username": "nobody", "password": "x"})

        assert "incorrectos" in response.content.decode()


class TestUpload:
    def test_upload_analyses_right_away(self, alice: Client) -> None:
        response = _upload(alice)

        assert "listo para aprobación" in _messages(response)[0]
        assert response.context["batch"].status == "PENDING_APPROVAL"
        assert response.context["batch"].created_by == "alice"

    def test_columns_with_other_names_are_identified_and_shown(self, alice: Client) -> None:
        response = _upload(alice, ES_CSV)

        mapping = response.context["batch"].column_mapping
        assert mapping.column_for("amount") == "Importe"
        assert "Nombre conocido" in response.content.decode()

    def test_blocking_errors_reject_automatically(self, alice: Client) -> None:
        response = _upload(alice, DIRTY_CSV)

        assert response.context["batch"].status == "REJECTED"
        assert response.context["errors"]
        assert "ERROR" in {severity for _, severity in response.context["rows"]}

    def test_unidentifiable_columns_are_explained(self, alice: Client) -> None:
        response = _upload(alice, b"foo,bar\n1,2\n")

        error = response.context["form"].errors["file"][0]
        assert "Importe" in error
        assert "foo, bar" in error

    def test_empty_file_message(self, alice: Client) -> None:
        response = _upload(alice, b"external_id,account,amount,currency,value_date\n")

        assert response.context["form"].errors["file"] == ["El archivo no contiene transacciones."]

    def test_actor_the_domain_does_not_accept(self, client: Client) -> None:
        User.objects.create_user(username="x" * 70, password=PASSWORD)
        client.login(username="x" * 70, password=PASSWORD)

        response = _upload(client)

        assert response.context["form"].non_field_errors() == [
            "No se pudo registrar el lote con estos datos."
        ]

    def test_blank_reference(self, alice: Client) -> None:
        response = _upload(alice, reference="   ")

        assert "reference" in response.context["form"].errors

    def test_oversized_file(self, alice: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(views, "MAX_UPLOAD_BYTES", 10)

        response = _upload(alice)

        assert "5 MB" in response.context["form"].errors["file"][0]

    def test_form_page(self, alice: Client) -> None:
        assert alice.get("/batches/new/").status_code == 200

    def test_empty_post_shows_required_fields(self, alice: Client) -> None:
        response = alice.post("/batches/new/")

        assert set(response.context["form"].errors) == {"reference", "file"}


class TestDecisions:
    def test_four_eyes_approval(self, alice: Client, bob: Client) -> None:
        url = _batch_url(_upload(alice))

        own = alice.get(url)
        assert own.context["is_owner"]
        response = alice.post(f"{url}approve/", follow=True)
        assert "cuatro ojos" in _messages(response)[0]

        response = bob.post(f"{url}approve/", follow=True)
        assert _messages(response) == ["Lote aprobado."]
        assert response.context["batch"].decided_by == "bob"

    def test_second_decision_is_refused(self, alice: Client, bob: Client) -> None:
        url = _batch_url(_upload(alice))
        bob.post(f"{url}approve/")

        response = bob.post(f"{url}reject/", {"reason": "late"}, follow=True)

        assert "cambió de estado" in _messages(response)[-1]

    def test_reject_with_reason(self, alice: Client, bob: Client) -> None:
        url = _batch_url(_upload(alice))

        response = bob.post(f"{url}reject/", {"reason": "Importes duplicados"}, follow=True)

        assert _messages(response) == ["Lote rechazado."]
        assert response.context["batch"].rejection_reason == "Importes duplicados"

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_reject_needs_a_reason(self, alice: Client, bob: Client, reason: str) -> None:
        url = _batch_url(_upload(alice))

        response = bob.post(f"{url}reject/", {"reason": reason}, follow=True)

        assert response.context["batch"].status == "PENDING_APPROVAL"
        assert "motivo" in _messages(response)[0].lower()

    def test_process_a_draft_uploaded_through_the_api(
        self, alice: Client, service: BatchService
    ) -> None:
        draft = service.register_batch(
            RegisterBatchCommand(reference="api", submitted_by="carol", content=CLEAN_CSV)
        )

        assert b"Analizar" in alice.get(f"/batches/{draft.id}/").content
        response = alice.post(f"/batches/{draft.id}/process/", follow=True)

        assert response.context["batch"].status == "PENDING_APPROVAL"

    def test_unknown_batch(self, alice: Client) -> None:
        response = alice.get(f"/batches/{uuid4()}/", follow=True)

        assert _messages(response) == ["El lote no existe."]

    def test_actions_require_post(self, alice: Client) -> None:
        assert alice.get(f"/batches/{uuid4()}/approve/").status_code == 405


class TestList:
    def test_filter_by_status(self, alice: Client) -> None:
        _upload(alice, reference="ok")
        _upload(alice, DIRTY_CSV, reference="bad")

        everything = alice.get("/")
        rejected = alice.get("/?status=REJECTED")
        bogus = alice.get("/?status=nope")

        assert [b.reference for b in everything.context["batches"]] == ["bad", "ok"]
        assert [b.reference for b in rejected.context["batches"]] == ["bad"]
        assert bogus.context["selected"] == ""
        assert everything.context["total"] == 2


class TestDemoUsers:
    def test_creates_the_demo_users_once(self) -> None:
        call_command("create_demo_users")
        call_command("create_demo_users")

        assert set(User.objects.values_list("username", flat=True)) == {"alice", "bob", "carol"}
        assert User.objects.get(username="bob").check_password("demo1234")

    @override_settings(DEMO_USERS=[])
    def test_does_nothing_in_production(self) -> None:
        call_command("create_demo_users")

        assert not User.objects.exists()
