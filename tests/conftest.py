import pytest

from ledger.application.services import BatchService


@pytest.fixture
def service() -> BatchService:
    # Imported lazily: the composition root pulls in Django models.
    from ledger.presentation.composition import build_batch_service

    return build_batch_service()
