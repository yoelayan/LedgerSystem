"""Django ORM models - persistence only, no business behaviour.

The database repeats the most critical invariants as CHECK constraints (defence in depth):
even a buggy migration script or a manual UPDATE cannot store an approval without an
approver, or an approval made by the batch's own submitter.
"""

from django.db import models
from django.db.models import F, Q

from ledger.domain.value_objects import BatchStatus

STATUS_CHOICES = [(status.value, status.value) for status in BatchStatus]


class BatchModel(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    reference = models.CharField(max_length=120)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, db_index=True)
    created_by = models.CharField(max_length=64)
    source_rows = models.JSONField()
    analysis_report = models.JSONField(null=True, blank=True)
    decided_by = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)  # noqa: DJ001
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ledger_batch"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=[status.value for status in BatchStatus]),
                name="ledger_batch_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(status="APPROVED") | Q(decided_by__isnull=False),
                name="ledger_batch_approved_has_approver",
            ),
            models.CheckConstraint(
                condition=~Q(status="APPROVED") | ~Q(decided_by=F("created_by")),
                name="ledger_batch_four_eyes",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.status})"
