"""Use cases for the batch lifecycle.

Transaction boundaries live here, on purpose: a use case is the unit of consistency.
Every state change follows the same pattern:

    with transaction.atomic():
        batch = repository.get_for_update(batch_id)   # SELECT ... FOR UPDATE
        batch.<transition>(...)                       # domain rules + state machine
        repository.save(batch)

Two concurrent requests on the same batch are therefore serialised by PostgreSQL: the
second one blocks on the row lock, then re-reads the *committed* state and fails with
InvalidStateTransitionError instead of silently overwriting the first decision.

Pragmatic trade-off: the application layer depends on `django.db.transaction` (and nothing
else from Django). Hiding it behind a Unit-of-Work port would add indirection without
adding value for a single-database service.
"""

import logging
from uuid import UUID

from django.db import transaction

from ledger.application.dtos import (
    ApproveBatchCommand,
    BatchDTO,
    RegisterBatchCommand,
    RejectBatchCommand,
)
from ledger.application.ports import BatchRepository, Clock, DatasetParser, TransactionAnalyzer
from ledger.domain.entities import Batch

logger = logging.getLogger(__name__)


class BatchService:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        parser: DatasetParser,
        analyzer: TransactionAnalyzer,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._analyzer = analyzer
        self._clock = clock

    def register_batch(self, command: RegisterBatchCommand) -> BatchDTO:
        rows = self._parser.parse(command.content)
        batch = Batch.register(
            reference=command.reference,
            created_by=command.submitted_by,
            rows=rows,
            now=self._clock(),
        )
        with transaction.atomic():
            self._repository.add(batch)
        logger.info("batch.registered", extra={"batch_id": str(batch.id), "rows": len(rows)})
        return BatchDTO.from_entity(batch)

    def process_batch(self, batch_id: UUID) -> BatchDTO:
        # Phase 1 - claim the batch. Committing PROCESSING makes a concurrent "process"
        # call fail fast with a 409 instead of analysing the same data twice.
        with transaction.atomic():
            batch = self._repository.get_for_update(batch_id)
            batch.start_processing()
            self._repository.save(batch)

        # Phase 2 - CPU-bound analysis *outside* any transaction: row locks must never be
        # held while pandas crunches data. If this raises, the error propagates (fail-fast)
        # and the batch stays visibly in PROCESSING for operators to investigate.
        report = self._analyzer.analyze(batch.rows)

        # Phase 3 - record the outcome under lock.
        with transaction.atomic():
            batch = self._repository.get_for_update(batch_id)
            batch.record_analysis(report, now=self._clock())
            self._repository.save(batch)
        logger.info(
            "batch.processed",
            extra={"batch_id": str(batch_id), "status": batch.status.value},
        )
        return BatchDTO.from_entity(batch)

    def approve_batch(self, command: ApproveBatchCommand) -> BatchDTO:
        with transaction.atomic():
            batch = self._repository.get_for_update(command.batch_id)
            batch.approve(approver=command.approver, now=self._clock())
            self._repository.save(batch)
        logger.info(
            "batch.approved",
            extra={"batch_id": str(command.batch_id), "approver": command.approver},
        )
        return BatchDTO.from_entity(batch)

    def reject_batch(self, command: RejectBatchCommand) -> BatchDTO:
        with transaction.atomic():
            batch = self._repository.get_for_update(command.batch_id)
            batch.reject(reviewer=command.reviewer, reason=command.reason, now=self._clock())
            self._repository.save(batch)
        logger.info(
            "batch.rejected",
            extra={"batch_id": str(command.batch_id), "reviewer": command.reviewer},
        )
        return BatchDTO.from_entity(batch)

    def get_batch(self, batch_id: UUID) -> BatchDTO:
        return BatchDTO.from_entity(self._repository.get(batch_id))

    def list_batches(self, limit: int = 50) -> list[BatchDTO]:
        return [BatchDTO.from_entity(b) for b in self._repository.list_recent(limit)]
