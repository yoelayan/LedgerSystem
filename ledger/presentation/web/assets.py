"""Front-end assets vendored in the repository, so charts work offline and behind proxies."""

from functools import cache
from pathlib import Path
from typing import Final

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

VENDOR: Final = Path(__file__).with_name("vendor")
CHART_JS: Final = "chart-4.4.4.umd.js"  # versioned name: safe to cache for a year


@cache
def _read(name: str) -> bytes:
    return (VENDOR / name).read_bytes()


@require_GET
def chart_js(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(_read(CHART_JS), content_type="application/javascript")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
