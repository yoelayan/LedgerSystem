from typing import Any

from django import template

register = template.Library()


@register.filter
def label(value: Any, labels: dict[str, str]) -> str:
    """`{{ batch.status|label:status_labels }}` -> "Aprobado"."""
    return labels.get(str(value), str(value))


@register.filter
def percent(value: float) -> str:
    return f"{round(value * 100)} %"
