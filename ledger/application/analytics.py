"""Use cases of the analytics module: overview and trends, timeline, AML, reconciliation.

All of them are read-only: they query the movements of analysed batches (by default,
approved ones) and run the domain engines on them. Nothing here changes a batch.
"""

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from ledger.application.dtos import ReconcileCommand, TransactionQuery
from ledger.application.ports import LedgerParser, TransactionReadModel
from ledger.domain.analytics.aml import AmlAlert, AmlPolicy, scan
from ledger.domain.analytics.reconciliation import (
    LedgerProblem,
    ReconciliationResult,
    reconcile,
)
from ledger.domain.analytics.timeline import TimelineDay, TimelineEntry, chronology, daily_balances
from ledger.domain.analytics.trends import (
    AccountActivity,
    Granularity,
    PeriodTotals,
    Totals,
    Trend,
    period_totals,
    summarize,
    top_accounts,
    trends,
)
from ledger.domain.value_objects import ColumnMapping

TOP_ACCOUNTS = 10


class OverviewDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: TransactionQuery
    granularity: Granularity
    transaction_count: int
    summary: dict[str, Totals]
    series: tuple[PeriodTotals, ...]
    trends: tuple[Trend, ...]
    top_accounts: tuple[AccountActivity, ...]


class TimelineDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: TransactionQuery
    days: tuple[TimelineDay, ...]
    entries: tuple[TimelineEntry, ...]


class AmlReportDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: TransactionQuery
    policy: AmlPolicy
    transaction_count: int
    alerts: tuple[AmlAlert, ...]


class ReconciliationDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: ReconciliationResult
    problems: tuple[LedgerProblem, ...]
    column_mapping: ColumnMapping
    direction_rule: str
    period_from: date | None  # movements compared: the ledger's dates +- the tolerance
    period_to: date | None
    ledger_entries: int
    date_tolerance_days: int


class AnalyticsService:
    def __init__(self, *, read_model: TransactionReadModel, ledger_parser: LedgerParser) -> None:
        self._read_model = read_model
        self._ledger_parser = ledger_parser

    def currencies(self) -> list[str]:
        return self._read_model.currencies()

    def overview(self, query: TransactionQuery, granularity: Granularity) -> OverviewDTO:
        transactions = self._read_model.transactions(query)
        series = period_totals(transactions, granularity)
        return OverviewDTO(
            query=query,
            granularity=granularity,
            transaction_count=len(transactions),
            summary=summarize(transactions),
            series=tuple(series),
            trends=tuple(trends(series)),
            top_accounts=tuple(top_accounts(transactions, TOP_ACCOUNTS)),
        )

    def timeline(self, query: TransactionQuery) -> TimelineDTO:
        days = daily_balances(self._read_model.transactions(query))
        events = self._read_model.batch_events(query)
        return TimelineDTO(query=query, days=tuple(days), entries=tuple(chronology(days, events)))

    def aml_alerts(self, query: TransactionQuery, policy: AmlPolicy | None = None) -> AmlReportDTO:
        policy = policy or AmlPolicy()
        transactions = self._read_model.transactions(query)
        return AmlReportDTO(
            query=query,
            policy=policy,
            transaction_count=len(transactions),
            alerts=tuple(scan(transactions, policy)),
        )

    def reconcile(self, command: ReconcileCommand) -> ReconciliationDTO:
        ledger = self._ledger_parser.parse(
            command.content,
            default_currency=command.default_currency,
            invert=command.invert_debit_credit,
        )
        tolerance = timedelta(days=command.date_tolerance_days)
        dates = [e.entry_date for e in ledger.entries]
        period_from = min(dates) - tolerance if dates else None
        period_to = max(dates) + tolerance if dates else None
        currencies = {e.currency for e in ledger.entries}
        # Only movements the ledger could contain: same period and currencies.
        transactions = (
            [
                t
                for t in self._read_model.transactions(
                    TransactionQuery(date_from=period_from, date_to=period_to)
                )
                if t.currency in currencies
            ]
            if dates
            else []
        )
        result = reconcile(
            transactions, ledger.entries, date_tolerance_days=command.date_tolerance_days
        )
        if dates:
            # The tolerance lets a movement dated just before the file's period match an
            # entry booked inside it. Unmatched, such a movement belongs to the adjacent
            # period's ledger, not to this one: it is not reported as missing here.
            first, last = min(dates), max(dates)
            result = result.model_copy(
                update={
                    "only_in_ledgersystem": tuple(
                        t for t in result.only_in_ledgersystem if first <= t.value_date <= last
                    )
                }
            )
        return ReconciliationDTO(
            result=result,
            problems=ledger.problems,
            column_mapping=ledger.column_mapping,
            direction_rule=ledger.direction_rule,
            period_from=period_from,
            period_to=period_to,
            ledger_entries=len(ledger.entries),
            date_tolerance_days=command.date_tolerance_days,
        )
