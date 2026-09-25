"""Ports: the contracts the application needs from the outside world."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ledger.domain.entities import Batch
from ledger.domain.value_objects import AnalysisReport, ParsedDataset, RawTransactionRow

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
