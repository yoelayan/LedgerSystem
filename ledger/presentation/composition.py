"""Composition root: the single place where ports are bound to concrete adapters."""

from django.utils import timezone

from ledger.application.analytics import AnalyticsService
from ledger.application.services import BatchService
from ledger.infrastructure.analysis.column_mapping import VectorColumnMapper
from ledger.infrastructure.analysis.csv_parser import PandasCsvParser
from ledger.infrastructure.analysis.ledger_parser import PandasLedgerParser
from ledger.infrastructure.analysis.transaction_analyzer import PandasTransactionAnalyzer
from ledger.infrastructure.read_models import DjangoTransactionReadModel
from ledger.infrastructure.repositories import DjangoBatchRepository


def build_batch_service() -> BatchService:
    return BatchService(
        repository=DjangoBatchRepository(),
        parser=PandasCsvParser(VectorColumnMapper()),
        analyzer=PandasTransactionAnalyzer(),
        clock=timezone.now,
    )


def build_analytics_service() -> AnalyticsService:
    return AnalyticsService(
        read_model=DjangoTransactionReadModel(), ledger_parser=PandasLedgerParser()
    )
