"""Django ORM models - persistence only, no business behaviour.

The database repeats the most critical invariants as CHECK constraints (defence in depth):
even a buggy migration script or a manual UPDATE cannot store an approval without an
approver, or an approval made by the batch's own submitter.
"""

from django.db import models
from django.db.models import F, Q

from ledger.domain.value_objects import BatchStatus, Direction

STATUS_CHOICES = [(status.value, status.value) for status in BatchStatus]
DIRECTION_CHOICES = [(direction.value, direction.value) for direction in Direction]


class BatchModel(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    reference = models.CharField(max_length=120)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, db_index=True)
    created_by = models.CharField(max_length=64)
    source_rows = models.JSONField()
    column_mapping = models.JSONField(null=True, blank=True)
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


class TransactionModel(models.Model):
    """Read model for analytics: one row per movement that passed a batch's analysis.

    Written once, when the batch is analysed; the batch's own status (approved, pending,
    rejected...) is read through the foreign key, so analytics can choose what counts.
    """

    batch = models.ForeignKey(BatchModel, on_delete=models.CASCADE, related_name="transactions")
    row_number = models.PositiveIntegerField()
    external_id = models.CharField(max_length=255)
    account = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.CharField(max_length=3)
    value_date = models.DateField()
    direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES)

    class Meta:
        db_table = "ledger_transaction"
        ordering = ["value_date", "batch_id", "row_number"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_number"], name="ledger_tx_unique_row"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="ledger_tx_amount_positive"),
            models.CheckConstraint(
                condition=Q(direction__in=[d.value for d in Direction]),
                name="ledger_tx_direction_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["value_date"], name="ledger_tx_date"),
            models.Index(fields=["account", "value_date"], name="ledger_tx_account_date"),
            models.Index(fields=["currency", "value_date"], name="ledger_tx_currency_date"),
            models.Index(fields=["external_id"], name="ledger_tx_external_id"),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} {self.amount} {self.currency}"
