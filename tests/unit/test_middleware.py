from http import HTTPStatus
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.http import HttpRequest, HttpResponse
from django.test.client import RequestFactory

from ledger.domain.exceptions import DomainError, InvalidStateTransitionError
from ledger.presentation.api.errors import RequestValidationError
from ledger.presentation.api.middleware import DomainExceptionMiddleware, status_for
from tests.unit.domain.test_exceptions import _concrete_errors


def _get_response(request: HttpRequest) -> HttpResponse:
    return HttpResponse()


middleware = DomainExceptionMiddleware(get_response=_get_response)
request = RequestFactory().post("/api/v1/batches/")


@pytest.mark.parametrize("error_type", _concrete_errors(), ids=lambda t: t.__name__)
def test_every_domain_error_has_an_http_status(error_type: type[DomainError]) -> None:
    assert status_for(error_type) in {
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
    }


def test_conflicts_map_to_409() -> None:
    error = InvalidStateTransitionError(uuid4(), "APPROVED", "approve")

    response = middleware.process_exception(request, error)

    assert response is not None
    assert response.status_code == 409


def test_request_validation_maps_to_400() -> None:
    response = middleware.process_exception(request, RequestValidationError("bad"))

    assert response is not None
    assert response.status_code == 400


def test_passes_requests_through() -> None:
    assert middleware(request).status_code == 200


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("bug"),
        KeyError("typo"),
        OperationalError("database is down"),
        DomainError("uncategorised domain error is a programming error"),
    ],
)
def test_anything_else_is_left_to_django_as_a_500(error: Exception) -> None:
    assert middleware.process_exception(request, error) is None
