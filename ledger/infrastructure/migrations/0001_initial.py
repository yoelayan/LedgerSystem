from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BatchModel",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("reference", models.CharField(max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "DRAFT"),
                            ("PROCESSING", "PROCESSING"),
                            ("PENDING_APPROVAL", "PENDING_APPROVAL"),
                            ("APPROVED", "APPROVED"),
                            ("REJECTED", "REJECTED"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("created_by", models.CharField(max_length=64)),
                ("source_rows", models.JSONField()),
                ("analysis_report", models.JSONField(blank=True, null=True)),
                ("decided_by", models.CharField(blank=True, max_length=64, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_reason", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "ledger_batch",
                "ordering": ["-created_at"],
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(
                            status__in=[
                                "DRAFT",
                                "PROCESSING",
                                "PENDING_APPROVAL",
                                "APPROVED",
                                "REJECTED",
                            ]
                        ),
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
                ],
            },
        ),
    ]
