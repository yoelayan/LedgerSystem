from django.apps import AppConfig


class LedgerInfrastructureConfig(AppConfig):
    name = "ledger.infrastructure"
    label = "ledger"
    verbose_name = "Ledger persistence"
    default_auto_field = "django.db.models.BigAutoField"
