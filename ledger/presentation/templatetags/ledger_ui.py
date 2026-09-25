from decimal import Decimal, InvalidOperation
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


@register.filter
def get(mapping: dict[str, Any], key: str) -> Any:
    """`{{ labels|get:field }}`: dictionary lookup with a variable key."""
    return mapping.get(key, key)


def format_money(value: Any) -> str:
    """Decimal("1234567.5") -> "1.234.567,50" (Spanish grouping, two decimals)."""
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    return f"{number:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


@register.filter
def money(value: Any) -> str:
    return format_money(value)
