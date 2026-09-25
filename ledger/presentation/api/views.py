"""Thin HTTP adapters: parse the request into a Pydantic command, call a use case,
serialise the resulting DTO. No business logic lives here.

Authentication is out of scope for this demo: the acting user is sent explicitly in the
payload (`submitted_by`, `approver`, `reviewer`).
"""

import json
from typing import Any
from uuid import UUID

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from pydantic import BaseModel, ValidationError

from ledger.application.dtos import (
    ApproveBatchCommand,
    BatchDTO,
    RegisterBatchCommand,
    RejectBatchCommand,
)
from ledger.presentation.api.errors import RequestValidationError
from ledger.presentation.composition import build_batch_service

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _validate[T: BaseModel](schema: type[T], payload: dict[str, Any]) -> T:
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        errors = json.loads(exc.json(include_url=False, include_input=False))
        raise RequestValidationError("Request payload is invalid.", errors=errors) from exc


def _json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RequestValidationError("Request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise RequestValidationError("Request body must be a JSON object.")
    return payload


def _batch_response(dto: BatchDTO, status: int = 200) -> JsonResponse:
    return JsonResponse(dto.model_dump(mode="json"), status=status)


@method_decorator(csrf_exempt, name="dispatch")
class BatchCollectionView(View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest) -> JsonResponse:
        batches = build_batch_service().list_batches()
        return JsonResponse({"items": [b.model_dump(mode="json") for b in batches]})

    def post(self, request: HttpRequest) -> JsonResponse:
        upload = request.FILES.get("file")
        if upload is None:
            raise RequestValidationError("A CSV file must be uploaded in the 'file' field.")
        if upload.size is not None and upload.size > MAX_UPLOAD_BYTES:
            raise RequestValidationError(
                "Uploaded file is too large.", max_bytes=MAX_UPLOAD_BYTES, size=upload.size
            )
        command = _validate(RegisterBatchCommand, {**request.POST.dict(), "content": upload.read()})
        return _batch_response(build_batch_service().register_batch(command), status=201)


class BatchDetailView(View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, batch_id: UUID) -> JsonResponse:
        return _batch_response(build_batch_service().get_batch(batch_id))


@method_decorator(csrf_exempt, name="dispatch")
class ProcessBatchView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, batch_id: UUID) -> JsonResponse:
        return _batch_response(build_batch_service().process_batch(batch_id))


@method_decorator(csrf_exempt, name="dispatch")
class ApproveBatchView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, batch_id: UUID) -> JsonResponse:
        command = _validate(ApproveBatchCommand, {**_json_body(request), "batch_id": batch_id})
        return _batch_response(build_batch_service().approve_batch(command))


@method_decorator(csrf_exempt, name="dispatch")
class RejectBatchView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, batch_id: UUID) -> JsonResponse:
        command = _validate(RejectBatchCommand, {**_json_body(request), "batch_id": batch_id})
        return _batch_response(build_batch_service().reject_batch(command))


class HealthView(View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest) -> JsonResponse:
        # A database outage must surface as a 500 here, not as a misleading "ok".
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
