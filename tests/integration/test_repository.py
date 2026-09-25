from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from ledger.domain.exceptions import BatchNotFoundError
from ledger.domain.value_objects import BatchStatus
from ledger.infrastructure.models import BatchModel
from ledger.infrastructure.repositories import DjangoBatchRepository
from tests.factories import NOW, SUBMITTER, blocking_report, make_batch

pytestmark = pytest.mark.django_db

repository = DjangoBatchRepository()


def test_round_trip_preserves_the_aggregate() -> None:
    batch = make_batch(BatchStatus.PROCESSING)
    batch.record_analysis(blocking_report(), now=NOW)
    repository.add(batch)

    assert repository.get(batch.id) == batch
    assert repository.get_for_update(batch.id) == batch


def test_save_persists_changes() -> None:
    batch = make_batch(BatchStatus.PENDING_APPROVAL)
    repository.add(batch)

    batch.approve(approver="bob", now=NOW)
    repository.save(batch)

    stored = repository.get(batch.id)
    assert stored.status is BatchStatus.APPROVED
    assert stored.decided_by == "bob"


def test_unknown_ids_raise_not_found() -> None:
    unknown = uuid4()

    with pytest.raises(BatchNotFoundError):
        repository.get(unknown)
    with pytest.raises(BatchNotFoundError):
        repository.get_for_update(unknown)
    with pytest.raises(BatchNotFoundError):
        repository.save(make_batch(id=unknown))


def test_list_recent_is_newest_first_and_limited() -> None:
    older = make_batch(created_at=NOW.replace(day=1))
    newer = make_batch(created_at=NOW.replace(day=2))
    newest = make_batch(created_at=NOW.replace(day=3))
    for batch in (older, newest, newer):
        repository.add(batch)

    assert [b.id for b in repository.list_recent(limit=2)] == [newest.id, newer.id]


class TestDatabaseConstraints:
    """Defence in depth: invariants hold even if someone bypasses the domain."""

    def test_approved_batch_requires_an_approver(self) -> None:
        batch = make_batch(BatchStatus.PENDING_APPROVAL)
        repository.add(batch)

        with pytest.raises(IntegrityError), transaction.atomic():
            BatchModel.objects.filter(pk=batch.id).update(status="APPROVED", decided_by=None)

    def test_submitter_cannot_be_the_approver(self) -> None:
        batch = make_batch(BatchStatus.PENDING_APPROVAL)
        repository.add(batch)

        with pytest.raises(IntegrityError), transaction.atomic():
            BatchModel.objects.filter(pk=batch.id).update(status="APPROVED", decided_by=SUBMITTER)

    def test_unknown_status_is_refused(self) -> None:
        batch = make_batch()
        repository.add(batch)

        with pytest.raises(IntegrityError), transaction.atomic():
            BatchModel.objects.filter(pk=batch.id).update(status="ARCHIVED")
