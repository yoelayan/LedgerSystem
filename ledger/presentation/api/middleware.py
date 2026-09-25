"""Maps *known* exceptions to clean HTTP responses - and nothing else.

Only domain error categories and request-validation errors are translated. Any other
exception (a bug, a database outage, a typo) makes `process_exception` return None, so
Django handles it as a 500 and logs the full traceback: failures are never silenced or
disguised as client errors.
"""

import logging
from collections.abc import Callable
from http import HTTPStatus
from typing import Final

from django.http import HttpRequest, HttpResponse

from ledger.domain.exceptions import (
    BusinessRuleError,
    ConflictError,
    DomainError,
    NotFoundError,
)
from ledger.presentation.api.errors import RequestValidationError, problem_response

logger = logging.getLogger(__name__)

STATUS_BY_ERROR_CATEGORY: Final[dict[type[Exception], HTTPStatus]] = {
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    BusinessRuleError: HTTPStatus.BAD_REQUEST,
    RequestValidationError: HTTPStatus.BAD_REQUEST,
}


def status_for(error_type: type[BaseException]) -> HTTPStatus | None:
    """Resolve the status through the MRO so every subclass inherits its category."""
    for klass in error_type.__mro__:
        status = STATUS_BY_ERROR_CATEGORY.get(klass)
        if status is not None:
            return status
    return None


class DomainExceptionMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not isinstance(exception, DomainError | RequestValidationError):
            return None
        status = status_for(type(exception))
        if status is None:
            # A DomainError outside every category is a programming error: fail loudly.
            return None
        logger.info(
            "request.rejected",
            extra={"code": exception.code, "status": int(status), "path": request.path},
        )
        return problem_response(
            status=status,
            code=exception.code,
            detail=exception.message,
            context=exception.context,
        )
