"""Load six months of demo batches (samples/analitica/) so the analytics have history.

Each batch is uploaded by alice and approved by bob through the real use cases, with the
clock set to the end of its month so the timeline shows realistic dates. Idempotent:
batches whose reference already exists are skipped. Refuses to run in production.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ledger.application.dtos import ApproveBatchCommand, RegisterBatchCommand
from ledger.application.services import BatchService
from ledger.infrastructure.analysis.csv_parser import PandasCsvParser
from ledger.infrastructure.analysis.transaction_analyzer import PandasTransactionAnalyzer
from ledger.infrastructure.models import BatchModel
from ledger.infrastructure.repositories import DjangoBatchRepository

SAMPLES = Path(settings.BASE_DIR) / "samples" / "analitica"


class Command(BaseCommand):
    help = "Upload and approve the demo batches in samples/analitica/."

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.ENVIRONMENT == "production":
            raise CommandError("Demo data is not loaded in production.")
        files = sorted(SAMPLES.glob("lote_*.csv"))
        if not files:
            raise CommandError(
                f"No demo batches in {SAMPLES}: run scripts/generate_demo_analytics.py."
            )
        for path in files:
            reference = f"Demo {path.stem.removeprefix('lote_').replace('_', '-')}"
            if BatchModel.objects.filter(reference=reference).exists():
                self.stdout.write(f"{reference}: already loaded")
                continue
            year, month = (int(part) for part in path.stem.split("_")[1:3])
            uploaded = datetime(year, month, 26, 9, 30, tzinfo=UTC)
            approved = datetime(year, month, 26, 16, 45, tzinfo=UTC)
            batch = _service(uploaded).register_batch(
                RegisterBatchCommand(
                    reference=reference, submitted_by="alice", content=path.read_bytes()
                )
            )
            processed = _service(uploaded).process_batch(batch.id)
            final = _service(approved).approve_batch(
                ApproveBatchCommand(batch_id=batch.id, approver="bob")
            )
            self.stdout.write(
                f"{reference}: {processed.row_count} rows, {final.status.value.lower()}"
            )


def _service(now: datetime) -> BatchService:
    return BatchService(
        repository=DjangoBatchRepository(),
        parser=PandasCsvParser(),
        analyzer=PandasTransactionAnalyzer(),
        clock=lambda: now,
    )
