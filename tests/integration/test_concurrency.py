"""Concurrency control with SELECT ... FOR UPDATE against a real PostgreSQL.

These tests need real, committed transactions on separate connections, so they use
`transaction=True` (no wrapping test transaction) and run work in threads - Django gives
every thread its own database connection.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import UUID

import pytest
from django.db import connection, transaction
from django.db.transaction import TransactionManagementError
from django.utils import timezone

from ledger.application.dtos import ApproveBatchCommand, RegisterBatchCommand
from ledger.application.services import BatchService
from ledger.domain.entities import Batch
from ledger.domain.exceptions import InvalidStateTransitionError
from ledger.domain.value_objects import BatchStatus
from ledger.infrastructure.analysis.csv_parser import PandasCsvParser
from ledger.infrastructure.analysis.transaction_analyzer import PandasTransactionAnalyzer
from ledger.infrastructure.repositories import DjangoBatchRepository
from tests.factories import CLEAN_CSV, SUBMITTER

pytestmark = pytest.mark.django_db(transaction=True)

WAIT_SECONDS = 10


def _in_own_connection[T](fn: Callable[[], T]) -> Callable[[], T]:
    """Run `fn` in a worker thread and always release that thread's DB connection."""

    def wrapper() -> T:
        try:
            return fn()
        finally:
            connection.close()

    return wrapper


def _service(repository: DjangoBatchRepository) -> BatchService:
    return BatchService(
        repository=repository,
        parser=PandasCsvParser(),
        analyzer=PandasTransactionAnalyzer(),
        clock=timezone.now,
    )


def _pending_batch() -> UUID:
    service = _service(DjangoBatchRepository())
    dto = service.register_batch(
        RegisterBatchCommand(reference="race", submitted_by=SUBMITTER, content=CLEAN_CSV)
    )
    service.process_batch(dto.id)
    return dto.id


def _approve(repository: DjangoBatchRepository, batch_id: UUID, approver: str) -> BatchStatus:
    command = ApproveBatchCommand(batch_id=batch_id, approver=approver)
    return _service(repository).approve_batch(command).status


class PausingRepository(DjangoBatchRepository):
    """Signals once a batch has been read, then waits for permission to continue.

    With `lock=True` the read is `SELECT ... FOR UPDATE`, so the row lock is *held* during
    the pause. With `lock=False` it is a plain SELECT: the naive, race-prone implementation.
    """

    def __init__(self, *, lock: bool) -> None:
        self.lock = lock
        self.has_read = threading.Event()
        self.may_continue = threading.Event()

    def get_for_update(self, batch_id: UUID) -> Batch:
        batch = super().get_for_update(batch_id) if self.lock else self.get(batch_id)
        self.has_read.set()
        if not self.may_continue.wait(WAIT_SECONDS):
            raise TimeoutError("test orchestration timed out")
        return batch


def test_two_simultaneous_approvals_only_one_wins() -> None:
    batch_id = _pending_batch()
    barrier = threading.Barrier(2)

    def approve(approver: str) -> Callable[[], BatchStatus]:
        def run() -> BatchStatus:
            barrier.wait(WAIT_SECONDS)
            return _approve(DjangoBatchRepository(), batch_id, approver)

        return _in_own_connection(run)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve("bob")), pool.submit(approve("carol"))]

    outcomes: list[BatchStatus | InvalidStateTransitionError] = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except InvalidStateTransitionError as exc:
            outcomes.append(exc)

    assert outcomes.count(BatchStatus.APPROVED) == 1
    assert sum(isinstance(o, InvalidStateTransitionError) for o in outcomes) == 1
    stored = DjangoBatchRepository().get(batch_id)
    assert stored.status is BatchStatus.APPROVED
    assert stored.decided_by in {"bob", "carol"}


def test_second_approver_blocks_on_the_row_lock_until_the_first_commits() -> None:
    """Deterministic proof that the lock serialises writers (no timing luck involved)."""
    batch_id = _pending_batch()
    first_repository = PausingRepository(lock=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first: Future[BatchStatus] = pool.submit(
            _in_own_connection(lambda: _approve(first_repository, batch_id, "bob"))
        )
        assert first_repository.has_read.wait(WAIT_SECONDS), "first approver never locked"

        second: Future[BatchStatus] = pool.submit(
            _in_own_connection(lambda: _approve(DjangoBatchRepository(), batch_id, "carol"))
        )
        # Bob holds the lock: Carol's SELECT ... FOR UPDATE cannot return yet.
        with pytest.raises(TimeoutError):
            second.result(timeout=1)

        first_repository.may_continue.set()
        assert first.result(timeout=WAIT_SECONDS) is BatchStatus.APPROVED

        # Once Bob commits, Carol reads the committed APPROVED state and is refused.
        with pytest.raises(InvalidStateTransitionError):
            second.result(timeout=WAIT_SECONDS)

    assert DjangoBatchRepository().get(batch_id).decided_by == "bob"


def test_without_the_lock_a_lost_update_happens() -> None:
    """Counter-example documenting the race condition that select_for_update prevents."""
    batch_id = _pending_batch()
    slow = PausingRepository(lock=False)
    fast = PausingRepository(lock=False)
    fast.may_continue.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        bob: Future[BatchStatus] = pool.submit(
            _in_own_connection(lambda: _approve(slow, batch_id, "bob"))
        )
        assert slow.has_read.wait(WAIT_SECONDS)  # bob has read PENDING_APPROVAL...

        carol = pool.submit(_in_own_connection(lambda: _approve(fast, batch_id, "carol")))
        assert carol.result(timeout=WAIT_SECONDS) is BatchStatus.APPROVED  # carol commits

        slow.may_continue.set()  # ...and bob approves his stale copy
        assert bob.result(timeout=WAIT_SECONDS) is BatchStatus.APPROVED

    # Both "succeeded": carol's approval was silently overwritten by bob's.
    assert DjangoBatchRepository().get(batch_id).decided_by == "bob"


def test_locking_read_outside_a_transaction_fails_loudly() -> None:
    batch_id = _pending_batch()

    with pytest.raises(TransactionManagementError):
        DjangoBatchRepository().get_for_update(batch_id)

    with transaction.atomic():
        assert DjangoBatchRepository().get_for_update(batch_id).id == batch_id
