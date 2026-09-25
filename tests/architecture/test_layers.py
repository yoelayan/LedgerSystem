"""Layering rules enforced as tests, so the architecture cannot silently erode."""

import ast
from pathlib import Path

import pytest

LEDGER = Path(__file__).resolve().parents[2] / "ledger"


def _imports(layer: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted((LEDGER / layer).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend((path.name, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.extend((path.name, f"{node.module}.{alias.name}") for alias in node.names)
    return found


def _violations(layer: str, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{file}: {module}"
        for file, module in _imports(layer)
        if any(module == f or module.startswith(f"{f}.") for f in forbidden)
    ]


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        (
            "domain",
            (
                "django",
                "pandas",
                "ledger.application",
                "ledger.infrastructure",
                "ledger.presentation",
            ),
        ),
        ("application", ("pandas", "ledger.infrastructure", "ledger.presentation")),
        ("infrastructure", ("ledger.presentation",)),
    ],
)
def test_layer_does_not_depend_on_outer_layers(layer: str, forbidden: tuple[str, ...]) -> None:
    assert _violations(layer, forbidden) == []


def test_application_only_uses_django_for_transactions() -> None:
    django_imports = {m for _, m in _imports("application") if m.startswith("django")}

    assert django_imports == {"django.db.transaction"}
