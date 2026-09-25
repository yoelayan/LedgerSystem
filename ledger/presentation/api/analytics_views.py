"""Analytics over HTTP: the same use cases as the web screens, as JSON."""

from typing import Any

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from pydantic import BaseModel

from ledger.application.dtos import ReconcileCommand, TransactionQuery
from ledger.domain.analytics.trends import Granularity
from ledger.presentation.api.errors import RequestValidationError
from ledger.presentation.api.views import MAX_UPLOAD_BYTES, validate
from ledger.presentation.composition import build_analytics_service

_QUERY_PARAMS = ("date_from", "date_to", "currency", "account", "include_pending")


def _query(request: HttpRequest) -> TransactionQuery:
    params = {k: v for k in _QUERY_PARAMS if (v := request.GET.get(k, "").strip())}
    return validate(TransactionQuery, params)


def _json(dto: BaseModel) -> JsonResponse:
    return JsonResponse(dto.model_dump(mode="json"))


class OverviewView(View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest) -> JsonResponse:
        raw = request.GET.get("granularity", Granularity.MONTH.value).upper()
        if raw not in Granularity.__members__:
            raise RequestValidationError("granularity must be DAY, WEEK or MONTH.", granularity=raw)
        return _json(build_analytics_service().overview(_query(request), Granularity(raw)))


class TimelineView(View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest) -> JsonResponse:
        return _json(build_analytics_service().timeline(_query(request)))


class AmlView(View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest) -> JsonResponse:
        return _json(build_analytics_service().aml_alerts(_query(request)))


@method_decorator(csrf_exempt, name="dispatch")
class ReconciliationView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        upload = request.FILES.get("file")
        if upload is None:
            raise RequestValidationError("An ERP export must be uploaded in the 'file' field.")
        if upload.size is not None and upload.size > MAX_UPLOAD_BYTES:
            raise RequestValidationError(
                "Uploaded file is too large.", max_bytes=MAX_UPLOAD_BYTES, size=upload.size
            )
        payload: dict[str, Any] = {**request.POST.dict(), "content": upload.read()}
        command = validate(ReconcileCommand, payload)
        return _json(build_analytics_service().reconcile(command))
