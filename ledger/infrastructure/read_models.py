"""Django implementation of the analytics read model (TransactionReadModel port)."""

from datetime import date

from django.db.models import Q, QuerySet

from ledger.application.dtos import TransactionQuery
from ledger.domain.analytics.timeline import BatchEvent, BatchEventKind
from ledger.domain.value_objects import BatchStatus, Direction, Transaction
from ledger.infrastructure.models import BatchModel, TransactionModel


class DjangoTransactionReadModel:
    def transactions(self, query: TransactionQuery) -> list[Transaction]:
        rows = _filtered(query).values(
            "batch_id",
            "row_number",
            "external_id",
            "account",
            "amount",
            "currency",
            "value_date",
            "direction",
        )
        # Built without per-row validation: the rows were validated when the batch was
        # analysed, and the table's CHECK constraints keep them valid.
        return [
            Transaction.model_construct(**{**row, "direction": Direction(row["direction"])})
            for row in rows.iterator(chunk_size=5000)
        ]

    def batch_events(self, query: TransactionQuery) -> list[BatchEvent]:
        batches = BatchModel.objects.filter(status__in=[s.value for s in query.statuses])
        if query.account or query.currency:
            batches = batches.filter(
                id__in=_filtered(query, dates=False).values("batch_id").distinct()
            )
        events: list[BatchEvent] = []
        for batch in batches.only(
            "id",
            "reference",
            "created_by",
            "created_at",
            "decided_by",
            "decided_at",
            "status",
            "rejection_reason",
        ):
            if _within(batch.created_at.date(), query):
                events.append(
                    BatchEvent(
                        batch_id=batch.id,
                        reference=batch.reference,
                        kind=BatchEventKind.UPLOADED,
                        at=batch.created_at,
                        actor=batch.created_by,
                    )
                )
            if batch.decided_at and batch.decided_by and _within(batch.decided_at.date(), query):
                approved = batch.status == BatchStatus.APPROVED
                events.append(
                    BatchEvent(
                        batch_id=batch.id,
                        reference=batch.reference,
                        kind=BatchEventKind.APPROVED if approved else BatchEventKind.REJECTED,
                        at=batch.decided_at,
                        actor=batch.decided_by,
                        detail="" if approved else (batch.rejection_reason or ""),
                    )
                )
        return sorted(events, key=lambda e: e.at)

    def currencies(self) -> list[str]:
        return sorted(
            TransactionModel.objects.values_list("currency", flat=True).distinct().order_by()
        )


def _filtered(query: TransactionQuery, *, dates: bool = True) -> QuerySet[TransactionModel]:
    rows = TransactionModel.objects.filter(
        batch__status__in=[s.value for s in query.statuses]
    ).order_by("value_date", "batch_id", "row_number")
    if dates and query.date_from:
        rows = rows.filter(value_date__gte=query.date_from)
    if dates and query.date_to:
        rows = rows.filter(value_date__lte=query.date_to)
    if query.currency:
        rows = rows.filter(currency=query.currency)
    if query.account:
        rows = rows.filter(Q(account__iexact=query.account.strip()))
    return rows


def _within(day: date, query: TransactionQuery) -> bool:
    return (query.date_from is None or day >= query.date_from) and (
        query.date_to is None or day <= query.date_to
    )
