"""Django ORM implementation of the BatchRepository port, plus entity <-> model mapping."""

from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from ledger.domain.entities import Batch
from ledger.domain.exceptions import BatchNotFoundError
from ledger.infrastructure.models import BatchModel


class DjangoBatchRepository:
    def add(self, batch: Batch) -> None:
        BatchModel.objects.create(id=batch.id, **_to_fields(batch))

    def get(self, batch_id: UUID) -> Batch:
        return _to_entity(self._fetch(BatchModel.objects.all(), batch_id))

    def get_for_update(self, batch_id: UUID) -> Batch:
        # select_for_update() -> SELECT ... FOR UPDATE: concurrent writers block here until
        # the holder's transaction commits. Django raises TransactionManagementError if this
        # is evaluated outside transaction.atomic(), so a missing boundary fails loudly.
        return _to_entity(self._fetch(BatchModel.objects.select_for_update(), batch_id))

    def save(self, batch: Batch) -> None:
        updated = BatchModel.objects.filter(pk=batch.id).update(
            **_to_fields(batch), updated_at=timezone.now()
        )
        if updated != 1:
            raise BatchNotFoundError(batch.id)

    def list_recent(self, limit: int) -> list[Batch]:
        return [_to_entity(m) for m in BatchModel.objects.order_by("-created_at")[:limit]]

    @staticmethod
    def _fetch(queryset: QuerySet[BatchModel], batch_id: UUID) -> BatchModel:
        try:
            return queryset.get(pk=batch_id)
        except BatchModel.DoesNotExist as exc:
            raise BatchNotFoundError(batch_id) from exc


def _to_fields(batch: Batch) -> dict[str, Any]:
    return {
        "reference": batch.reference,
        "status": batch.status.value,
        "created_by": batch.created_by,
        "created_at": batch.created_at,
        "source_rows": [row.model_dump(mode="json") for row in batch.rows],
        "column_mapping": (
            batch.column_mapping.model_dump(mode="json") if batch.column_mapping else None
        ),
        "analysis_report": batch.analysis.model_dump(mode="json") if batch.analysis else None,
        "decided_by": batch.decided_by,
        "decided_at": batch.decided_at,
        "rejection_reason": batch.rejection_reason,
    }


def _to_entity(model: BatchModel) -> Batch:
    return Batch.model_validate(
        {
            "id": model.id,
            "reference": model.reference,
            "status": model.status,
            "created_by": model.created_by,
            "created_at": model.created_at,
            "rows": model.source_rows,
            "column_mapping": model.column_mapping,
            "analysis": model.analysis_report,
            "decided_by": model.decided_by,
            "decided_at": model.decided_at,
            "rejection_reason": model.rejection_reason,
        }
    )
