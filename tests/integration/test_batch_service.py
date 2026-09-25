"""Use cases end-to-end against PostgreSQL (real repository, parser and analyzer)."""

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

from ledger.application.dtos import ApproveBatchCommand, RegisterBatchCommand, RejectBatchCommand
from ledger.application.services import BatchService
from ledger.domain.exceptions import (
    BatchNotFoundError,
    InvalidStateTransitionError,
    MalformedDatasetError,
    RejectionReasonRequiredError,
    SelfApprovalError,
)
from ledger.domain.value_objects import AnalysisReport, BatchStatus, RawTransactionRow
from ledger.infrastructure.analysis.csv_parser import PandasCsvParser
from ledger.infrastructure.models import BatchModel
from ledger.infrastructure.repositories import DjangoBatchRepository
from tests.factories import CLEAN_CSV, DIRTY_CSV, SUBMITTER

pytestmark = pytest.mark.django_db


def _register(service: BatchService, content: bytes = CLEAN_CSV) -> UUID:
    dto = service.register_batch(
        RegisterBatchCommand(reference="sept", submitted_by=SUBMITTER, content=content)
    )
    return dto.id


def _pending(service: BatchService) -> UUID:
    batch_id = _register(service)
    service.process_batch(batch_id)
    return batch_id


class TestRegister:
    def test_registers_a_draft_batch(self, service: BatchService) -> None:
        dto = service.register_batch(
            RegisterBatchCommand(reference=" sept ", submitted_by=" alice ", content=CLEAN_CSV)
        )

        assert dto.status is BatchStatus.DRAFT
        assert dto.reference == "sept"
        assert dto.created_by == "alice"
        assert dto.row_count == 3
        assert service.get_batch(dto.id) == dto

    def test_malformed_dataset_persists_nothing(self, service: BatchService) -> None:
        with pytest.raises(MalformedDatasetError):
            service.register_batch(
                RegisterBatchCommand(reference="x", submitted_by=SUBMITTER, content=b"a,b\n1,2")
            )

        assert BatchModel.objects.count() == 0


class TestProcess:
    def test_clean_batch_awaits_approval(self, service: BatchService) -> None:
        dto = service.process_batch(_register(service))

        assert dto.status is BatchStatus.PENDING_APPROVAL
        assert dto.analysis is not None
        assert dto.analysis.valid_rows == 3

    def test_dirty_batch_is_rejected_automatically(self, service: BatchService) -> None:
        dto = service.process_batch(_register(service, DIRTY_CSV))

        assert dto.status is BatchStatus.REJECTED
        assert dto.decided_by == "system"
        assert dto.analysis is not None
        assert {i.code for i in dto.analysis.blocking_issues} == {
            "DUPLICATE_EXTERNAL_ID",
            "INVALID_AMOUNT",
        }

    def test_cannot_process_twice(self, service: BatchService) -> None:
        batch_id = _pending(service)

        with pytest.raises(InvalidStateTransitionError):
            service.process_batch(batch_id)

    def test_unexpected_analyzer_failure_propagates_and_stays_visible(self) -> None:
        """Fail-fast: an infrastructure bug is not swallowed nor turned into a rejection."""

        class ExplodingAnalyzer:
            def analyze(self, rows: Sequence[RawTransactionRow]) -> AnalysisReport:
                raise RuntimeError("analysis engine crashed")

        service = BatchService(
            repository=DjangoBatchRepository(),
            parser=PandasCsvParser(),
            analyzer=ExplodingAnalyzer(),
            clock=timezone.now,
        )
        batch_id = _register(service)

        with pytest.raises(RuntimeError, match="analysis engine crashed"):
            service.process_batch(batch_id)

        assert service.get_batch(batch_id).status is BatchStatus.PROCESSING


class TestDecisions:
    def test_approve(self, service: BatchService) -> None:
        batch_id = _pending(service)

        dto = service.approve_batch(ApproveBatchCommand(batch_id=batch_id, approver="bob"))

        assert dto.status is BatchStatus.APPROVED
        assert dto.decided_by == "bob"
        assert BatchModel.objects.get(pk=batch_id).status == "APPROVED"

    def test_self_approval_is_refused_and_nothing_changes(self, service: BatchService) -> None:
        batch_id = _pending(service)

        with pytest.raises(SelfApprovalError):
            service.approve_batch(ApproveBatchCommand(batch_id=batch_id, approver=SUBMITTER))

        assert BatchModel.objects.get(pk=batch_id).status == "PENDING_APPROVAL"

    def test_reject(self, service: BatchService) -> None:
        batch_id = _pending(service)

        dto = service.reject_batch(
            RejectBatchCommand(batch_id=batch_id, reviewer="bob", reason="Wrong period")
        )

        assert dto.status is BatchStatus.REJECTED
        assert dto.rejection_reason == "Wrong period"

    def test_reject_without_reason(self, service: BatchService) -> None:
        batch_id = _pending(service)

        with pytest.raises(RejectionReasonRequiredError):
            service.reject_batch(RejectBatchCommand(batch_id=batch_id, reviewer="bob", reason=" "))

    def test_approving_a_draft_is_a_conflict(self, service: BatchService) -> None:
        batch_id = _register(service)

        with pytest.raises(InvalidStateTransitionError):
            service.approve_batch(ApproveBatchCommand(batch_id=batch_id, approver="bob"))

    def test_unknown_batch(self, service: BatchService) -> None:
        with pytest.raises(BatchNotFoundError):
            service.approve_batch(ApproveBatchCommand(batch_id=uuid4(), approver="bob"))


def test_list_batches(service: BatchService) -> None:
    first = _register(service)
    second = _register(service)

    assert {b.id for b in service.list_batches()} == {first, second}


def test_detail_includes_rows_and_column_mapping(service: BatchService) -> None:
    batch_id = _register(service)

    detail = service.get_batch_detail(batch_id)

    assert [row.external_id for row in detail.rows] == ["TX-1", "TX-2", "TX-3"]
    assert detail.column_mapping is not None
    assert detail.column_mapping.column_for("currency") == "currency"
