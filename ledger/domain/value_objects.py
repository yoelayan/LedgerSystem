"""Value objects and domain policies (immutable, validated by Pydantic)."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_ROWS_PER_BATCH: Final = 10_000
REQUIRED_COLUMNS: Final = ("external_id", "account", "amount", "currency", "value_date")
SUPPORTED_CURRENCIES: Final = frozenset({"USD", "EUR", "GBP", "MXN", "COP"})
MAX_FRACTION_DIGITS: Final = 2
SYSTEM_ACTOR: Final = "system"

ActorId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class BatchStatus(StrEnum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RawTransactionRow(BaseModel):
    """A dataset row exactly as received.

    Values are deliberately kept as strings: detecting bad amounts, dates or currencies is
    the job of the analysis step, which must *report* them instead of refusing the upload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int = Field(ge=1)
    external_id: str
    account: str
    amount: str
    currency: str
    value_date: str


class Severity(StrEnum):
    ERROR = "ERROR"  # blocks the batch: it is rejected automatically
    WARNING = "WARNING"  # informative: surfaced to the approver


class IssueCode(StrEnum):
    MISSING_VALUE = "MISSING_VALUE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    NON_POSITIVE_AMOUNT = "NON_POSITIVE_AMOUNT"
    EXCESSIVE_PRECISION = "EXCESSIVE_PRECISION"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    INVALID_VALUE_DATE = "INVALID_VALUE_DATE"
    DUPLICATE_EXTERNAL_ID = "DUPLICATE_EXTERNAL_ID"
    AMOUNT_OUTLIER = "AMOUNT_OUTLIER"


class AnalysisIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int = Field(ge=1)
    code: IssueCode
    severity: Severity
    message: str


class AnalysisReport(BaseModel):
    """Outcome of analysing a batch's dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    totals_by_currency: dict[str, Decimal]
    issues: tuple[AnalysisIssue, ...] = ()

    @model_validator(mode="after")
    def _valid_rows_within_total(self) -> Self:
        if self.valid_rows > self.total_rows:
            raise ValueError("valid_rows cannot exceed total_rows")
        return self

    @property
    def blocking_issues(self) -> tuple[AnalysisIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[AnalysisIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def has_blocking_issues(self) -> bool:
        return bool(self.blocking_issues)
