"""Ports: the contracts the application needs from the outside world."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ledger.application.dtos import TransactionQuery
from ledger.domain.analytics.reconciliation import ParsedLedger
from ledger.domain.analytics.timeline import BatchEvent
from ledger.domain.entities import Batch
from ledger.domain.value_objects import (
    AnalysisReport,
    ParsedDataset,
    RawTransactionRow,
    Transaction,
)

Clock = Callable[[], datetime]


class BatchRepository(Protocol):
    def add(self, batch: Batch) -> None: ...

    def get(self, batch_id: UUID) -> Batch:
        """Plain read. Raises BatchNotFoundError."""
        ...

    def get_for_update(self, batch_id: UUID) -> Batch:
        """Read and take a pessimistic row lock until the surrounding transaction ends.

        Must be called inside a transaction. Raises BatchNotFoundError.
        """
        ...

    def save(self, batch: Batch) -> None: ...

    def list_recent(self, limit: int) -> list[Batch]: ...


class DatasetParser(Protocol):
    def parse(self, content: bytes, *, signed_amounts: bool = False) -> ParsedDataset:
        """Identify the columns and read the rows.

        `signed_amounts`: the uploader declares that negative amounts are outflows.

        Raises MalformedDatasetError / EmptyBatchError / BatchTooLargeError.
        """
        ...


class TransactionAnalyzer(Protocol):
    def analyze(self, rows: Sequence[RawTransactionRow]) -> AnalysisReport: ...


class TransactionReadModel(Protocol):
    """Queries over the movements of analysed batches (the analytics read model)."""

    def transactions(self, query: TransactionQuery) -> list[Transaction]: ...

    def batch_events(self, query: TransactionQuery) -> list[BatchEvent]:
        """Uploads and decisions of the batches involved, within the query's dates."""
        ...

    def currencies(self) -> list[str]: ...


class LedgerParser(Protocol):
    def parse(self, content: bytes, *, default_currency: str, invert: bool = False) -> ParsedLedger:
        """Raises MalformedDatasetError / EmptyBatchError when the file is unusable."""
        ...
