"""HTTP contract: status codes and problem+json bodies produced by the middleware."""

import json
from typing import Any
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.client import RequestFactory

from ledger.presentation.api import views
from ledger.presentation.api.errors import PROBLEM_CONTENT_TYPE, server_error
from tests.factories import CLEAN_CSV, SUBMITTER

pytestmark = pytest.mark.django_db

BASE = "/api/v1"


def _upload(client: Client, content: bytes = CLEAN_CSV, **fields: str) -> Any:
    data: dict[str, Any] = {"reference": "sept", "submitted_by": SUBMITTER, **fields}
    data["file"] = SimpleUploadedFile("batch.csv", content, content_type="text/csv")
    return client.post(f"{BASE}/batches/", data)


def _post_json(client: Client, url: str, payload: Any) -> Any:
    return client.post(url, json.dumps(payload), content_type="application/json")


def _pending(client: Client) -> str:
    batch_id: str = _upload(client).json()["id"]
    assert client.post(f"{BASE}/batches/{batch_id}/process/").status_code == 200
    return batch_id


def _assert_problem(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    # json.loads instead of response.json(): the latter only exists on test-client responses.
    body: dict[str, Any] = json.loads(response.content)
    assert body["status"] == status
    assert body["code"] == code
    return body


def test_health(client: Client) -> None:
    assert client.get(f"{BASE}/health/").json() == {"status": "ok"}


def test_full_lifecycle(client: Client) -> None:
    created = _upload(client)
    assert created.status_code == 201
    batch_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"

    processed = client.post(f"{BASE}/batches/{batch_id}/process/")
    assert processed.json()["status"] == "PENDING_APPROVAL"
    assert processed.json()["analysis"]["totals_by_currency"] == {"EUR": "99.99", "USD": "250.50"}

    approved = _post_json(client, f"{BASE}/batches/{batch_id}/approve/", {"approver": "bob"})
    assert approved.status_code == 200
    assert approved.json()["decided_by"] == "bob"

    detail = client.get(f"{BASE}/batches/{batch_id}/")
    assert detail.json()["status"] == "APPROVED"
    assert [b["id"] for b in client.get(f"{BASE}/batches/").json()["items"]] == [batch_id]


def test_reject(client: Client) -> None:
    batch_id = _pending(client)

    response = _post_json(
        client, f"{BASE}/batches/{batch_id}/reject/", {"reviewer": "bob", "reason": "Wrong"}
    )

    assert response.status_code == 200
    assert response.json()["rejection_reason"] == "Wrong"


class TestDomainErrorsMapping:
    def test_double_approval_is_409(self, client: Client) -> None:
        batch_id = _pending(client)
        _post_json(client, f"{BASE}/batches/{batch_id}/approve/", {"approver": "bob"})

        response = _post_json(client, f"{BASE}/batches/{batch_id}/approve/", {"approver": "carol"})

        body = _assert_problem(response, 409, "INVALID_STATE_TRANSITION")
        assert body["context"]["current_status"] == "APPROVED"

    def test_self_approval_is_400(self, client: Client) -> None:
        batch_id = _pending(client)

        response = _post_json(client, f"{BASE}/batches/{batch_id}/approve/", {"approver": SUBMITTER})

        _assert_problem(response, 400, "SELF_APPROVAL_FORBIDDEN")

    def test_blank_rejection_reason_is_400(self, client: Client) -> None:
        batch_id = _pending(client)

        response = _post_json(
            client, f"{BASE}/batches/{batch_id}/reject/", {"reviewer": "bob", "reason": "  "}
        )

        _assert_problem(response, 400, "REJECTION_REASON_REQUIRED")

    def test_malformed_csv_is_400(self, client: Client) -> None:
        response = _upload(client, content=b"foo,bar\n1,2\n")

        body = _assert_problem(response, 400, "MALFORMED_DATASET")
        assert "amount" in body["context"]["missing_columns"]

    def test_unknown_batch_is_404(self, client: Client) -> None:
        _assert_problem(client.get(f"{BASE}/batches/{uuid4()}/"), 404, "BATCH_NOT_FOUND")


class TestRequestValidation:
    def test_missing_file(self, client: Client) -> None:
        response = client.post(f"{BASE}/batches/", {"reference": "x", "submitted_by": "alice"})

        _assert_problem(response, 400, "REQUEST_VALIDATION_FAILED")

    def test_oversized_file(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(views, "MAX_UPLOAD_BYTES", 10)

        body = _assert_problem(_upload(client), 400, "REQUEST_VALIDATION_FAILED")
        assert body["context"]["max_bytes"] == 10

    def test_schema_errors_are_listed(self, client: Client) -> None:
        response = _upload(client, submitted_by="")

        body = _assert_problem(response, 400, "REQUEST_VALIDATION_FAILED")
        assert body["context"]["errors"][0]["loc"] == ["submitted_by"]

    @pytest.mark.parametrize("raw_body", ["not json", "[1, 2]"])
    def test_body_must_be_a_json_object(self, client: Client, raw_body: str) -> None:
        response = client.post(
            f"{BASE}/batches/{uuid4()}/approve/", raw_body, content_type="application/json"
        )

        _assert_problem(response, 400, "REQUEST_VALIDATION_FAILED")

    def test_unknown_fields_are_refused(self, client: Client) -> None:
        response = _post_json(
            client, f"{BASE}/batches/{uuid4()}/approve/", {"approver": "bob", "role": "admin"}
        )

        _assert_problem(response, 400, "REQUEST_VALIDATION_FAILED")


class TestFailFast:
    def test_unexpected_errors_are_not_swallowed(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken_service() -> None:
            raise RuntimeError("wiring bug")

        monkeypatch.setattr(views, "build_batch_service", broken_service)

        # The middleware ignores it, so Django's test client re-raises it here; in
        # production it becomes a logged 500 rendered by `server_error`.
        with pytest.raises(RuntimeError, match="wiring bug"):
            client.get(f"{BASE}/batches/")

    def test_500_handler_does_not_leak_details(self) -> None:
        response = server_error(RequestFactory().get("/"))

        body = _assert_problem(response, 500, "INTERNAL_ERROR")
        assert "wiring" not in json.dumps(body)
