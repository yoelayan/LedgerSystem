"""Live demo: two approvers hit "approve" on the same batch at the same instant.

    docker compose exec web python manage.py simulate_concurrent_approval

Both threads are released together by a barrier. SELECT ... FOR UPDATE serialises them:
exactly one approval wins, the other re-reads the committed APPROVED state and receives
InvalidStateTransitionError (HTTP 409 through the API).
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ledger.application.dtos import ApproveBatchCommand, RegisterBatchCommand
from ledger.domain.exceptions import InvalidStateTransitionError
from ledger.presentation.composition import build_batch_service

DEMO_CSV = b"""external_id,account,amount,currency,value_date
TX-1,ES91-0001,1250.00,EUR,2026-09-01
TX-2,ES91-0002,980.50,EUR,2026-09-01
TX-3,ES91-0003,1100.00,EUR,2026-09-02
"""


class Command(BaseCommand):
    help = "Simulate two simultaneous approvals of the same batch."

    def handle(self, *args: Any, **options: Any) -> None:
        service = build_batch_service()
        batch = service.register_batch(
            RegisterBatchCommand(
                reference="concurrency-demo", submitted_by="alice", content=DEMO_CSV
            )
        )
        service.process_batch(batch.id)
        self.stdout.write(f"Batch {batch.id} is PENDING_APPROVAL. Racing two approvers...")

        barrier = threading.Barrier(2)

        def approve(approver: str) -> str:
            try:
                barrier.wait()
                result = service.approve_batch(
                    ApproveBatchCommand(batch_id=batch.id, approver=approver)
                )
            except InvalidStateTransitionError as exc:
                return f"{approver}: REFUSED (409) - {exc.message}"
            else:
                return f"{approver}: {result.status.value}"
            finally:
                connection.close()  # each thread owns its own DB connection

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, ["bob", "carol"]))

        for outcome in outcomes:
            self.stdout.write(f"  {outcome}")

        final = service.get_batch(batch.id)
        winners = [o for o in outcomes if o.endswith("APPROVED")]
        if len(winners) != 1:
            raise CommandError(f"Expected exactly one winning approval, got {outcomes}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Final state: {final.status.value} by {final.decided_by}. "
                "Exactly one approval was applied."
            )
        )
