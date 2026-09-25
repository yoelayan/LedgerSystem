"""Error contract of the HTTP API (RFC 9457 "problem details")."""

from http import HTTPStatus
from typing import Any, ClassVar

from django.http import HttpRequest, JsonResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"


class RequestValidationError(Exception):
    """The HTTP request itself is unusable (bad JSON, missing file, schema mismatch).

    Lives in the presentation layer: it is about the transport, not about the business.
    """

    code: ClassVar[str] = "REQUEST_VALIDATION_FAILED"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


def problem_response(
    *, status: int, code: str, detail: str, context: dict[str, Any] | None = None
) -> JsonResponse:
    return JsonResponse(
        {
            "type": "about:blank",
            "title": HTTPStatus(status).phrase,
            "status": status,
            "code": code,
            "detail": detail,
            "context": context or {},
        },
        status=status,
        content_type=PROBLEM_CONTENT_TYPE,
    )


def server_error(request: HttpRequest) -> JsonResponse:
    """handler500: the traceback is already logged by Django; never leak it to clients."""
    return problem_response(
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        detail="An unexpected error occurred. It has been logged.",
    )
