from django.apps import AppConfig


class LedgerPresentationConfig(AppConfig):
    """Registered only so Django discovers the management commands of this layer."""

    name = "ledger.presentation"
    label = "ledger_presentation"
    verbose_name = "Ledger API & CLI"
