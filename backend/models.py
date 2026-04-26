"""
models.py — Manual Adjustments Agent (MAA)
==========================================
All Pydantic v2 data models for the pipeline.

Hierarchy:
    COAAccount                  ← one row from chart_of_accounts.csv
    JournalLine                 ← one debit or credit line inside an entry
    JournalEntry                ← one complete journal entry (multiple lines)
    CheckResult                 ← output of one deterministic validation check
    ValidatorHandoff            ← typed contract passed from Validator → LLM
    PlainEnglishExplanation     ← structured output FROM the LLM (Pydantic-parsed)
    ValidationResult            ← final verdict for one entry
    AuditLogRecord              ← what gets written to validation_log.jsonl
    PipelineRunSummary          ← top-level run stats written at pipeline end
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    ConfigDict,
)


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 — ENUMERATIONS
# All string enums so they serialise cleanly to JSON.
# ──────────────────────────────────────────────────────────────────────────────

class EntryStatus(str, Enum):
    """Terminal status of a journal entry after the full pipeline runs."""
    ACCEPTED    = "ACCEPTED"     # All checks passed; no LLM call needed.
    REJECTED    = "REJECTED"     # One or more checks failed; LLM explained why.
    QUARANTINED = "QUARANTINED"  # Checks failed AND LLM call itself failed.


class ErrorCode(str, Enum):
    """
    Machine-readable error codes emitted by the deterministic validator.
    These codes drive the LLM prompt — the LLM is given these codes plus
    the raw entry and asked to produce a human explanation.
    """
    ERR_UNBALANCED       = "ERR_UNBALANCED"        # sum(debits) != sum(credits)
    ERR_INVALID_ACCOUNT  = "ERR_INVALID_ACCOUNT"   # account_code not in COA
    ERR_IC_CIRCULAR      = "ERR_IC_CIRCULAR"        # same IC account on both sides
    ERR_SCHEMA_INVALID   = "ERR_SCHEMA_INVALID"     # Pydantic / field-level failure
    ERR_DUPLICATE_ID     = "ERR_DUPLICATE_ID"       # entry_id already processed
    ERR_BOTH_SIDES       = "ERR_BOTH_SIDES"         # a line has debit AND credit > 0
    ERR_ZERO_LINE        = "ERR_ZERO_LINE"          # a line has debit=0 AND credit=0


class LLMStatus(str, Enum):
    """Result of the LLM Explainer call for a given entry."""
    OK            = "OK"            # Called and parsed successfully.
    TIMEOUT       = "TIMEOUT"       # API call timed out after retries.
    PARSE_FAILURE = "PARSE_FAILURE" # Response was not valid JSON / schema mismatch.
    RATE_LIMITED  = "RATE_LIMITED"  # HTTP 429 received after retries.
    NOT_CALLED    = "NOT_CALLED"    # Entry passed all checks; LLM skipped.
    SKIPPED       = "SKIPPED"       # LLM disabled via config flag.


class CheckName(str, Enum):
    """Names of each deterministic check, used in audit log check arrays."""
    SCHEMA       = "SCHEMA"        # Pydantic field validation
    BALANCE      = "BALANCE"       # debits == credits
    COA_LOOKUP   = "COA_LOOKUP"    # all account codes exist in COA
    IC_CIRCULAR  = "IC_CIRCULAR"   # circular intercompany detection
    DUPLICATE_ID = "DUPLICATE_ID"  # entry_id uniqueness


class AccountType(str, Enum):
    """Standard chart-of-accounts account classification."""
    ASSET        = "ASSET"
    LIABILITY    = "LIABILITY"
    EQUITY       = "EQUITY"
    REVENUE      = "REVENUE"
    EXPENSE      = "EXPENSE"


class NormalBalance(str, Enum):
    """Which side increases the account under double-entry bookkeeping."""
    DEBIT  = "DEBIT"
    CREDIT = "CREDIT"


class RiskSeverity(str, Enum):
    """Severity label attached to the LLM explanation."""
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 — CHART OF ACCOUNTS MODEL
# Represents one row loaded from chart_of_accounts.csv at pipeline startup.
# ──────────────────────────────────────────────────────────────────────────────

class COAAccount(BaseModel):
    """
    A single row in the Chart of Accounts.

    Loaded once at startup. The validator receives a Dict[str, COAAccount]
    keyed by account_code for O(1) lookups during COA_LOOKUP checks.
    """
    model_config = ConfigDict(frozen=True)  # Immutable after loading.

    account_code: str = Field(
        ...,
        description="Unique alphanumeric code, e.g. '1000', '7001-IC'.",
        min_length=1,
        max_length=20,
    )
    account_name: str = Field(
        ...,
        description="Human-readable account name.",
        min_length=1,
        max_length=120,
    )
    account_type: AccountType = Field(
        ...,
        description="High-level classification of the account.",
    )
    is_intercompany: bool = Field(
        default=False,
        description=(
            "True if this account is used for intercompany transactions. "
            "Required for ERR_IC_CIRCULAR detection. If this column is absent "
            "from the CSV, all accounts default to False and IC checks are skipped."
        ),
    )
    is_active: bool = Field(
        default=True,
        description="Inactive accounts fail COA_LOOKUP even if the code exists.",
    )
    normal_balance: NormalBalance = Field(
        ...,
        description="Whether this account normally carries a Debit or Credit balance.",
    )
    parent_code: Optional[str] = Field(
        default=None,
        description="Parent account code for hierarchical COAs. Not validated by MAA.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 — JOURNAL ENTRY MODELS
# Raw input from manual_adjustments.json.
# ──────────────────────────────────────────────────────────────────────────────

class JournalLine(BaseModel):
    """
    A single debit or credit line within a JournalEntry.

    Invariants enforced at model level (before any validator runs):
      - debit and credit are mutually exclusive (ERR_BOTH_SIDES).
      - at least one of debit/credit must be > 0 (ERR_ZERO_LINE).
      - amounts are stored as Decimal, rounded to 2dp.
    """
    line_number: int = Field(
        ...,
        description="1-indexed position of this line within the entry.",
        ge=1,
    )
    account_code: str = Field(
        ...,
        description="Account code that must exist in the COA.",
        min_length=1,
        max_length=20,
    )
    debit: Decimal = Field(
        default=Decimal("0.00"),
        description="Debit amount. Must be >= 0. Cannot be non-zero when credit is non-zero.",
        ge=0,
    )
    credit: Decimal = Field(
        default=Decimal("0.00"),
        description="Credit amount. Must be >= 0. Cannot be non-zero when debit is non-zero.",
        ge=0,
    )
    memo: Optional[str] = Field(
        default=None,
        description="Free-text note for this line. Passed to LLM for context.",
        max_length=500,
    )

    @field_validator("debit", "credit", mode="before")
    @classmethod
    def coerce_to_decimal(cls, v: Any) -> Decimal:
        """Accept int/float/str from JSON; normalise to 2dp Decimal."""
        try:
            return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            raise ValueError(f"Cannot coerce '{v}' to a monetary Decimal.")

    @model_validator(mode="after")
    def check_mutual_exclusion(self) -> "JournalLine":
        """
        A line must be EITHER a debit OR a credit, never both and never neither.
        Violations are tagged ERR_BOTH_SIDES or ERR_ZERO_LINE by the validator.
        We raise here so Pydantic catches it before the validator even runs.
        """
        d, c = self.debit, self.credit
        if d > 0 and c > 0:
            raise ValueError(
                f"Line {self.line_number} (account {self.account_code}): "
                f"A line cannot carry both a debit ({d}) and a credit ({c}). "
                f"Split into two separate lines."
            )
        if d == 0 and c == 0:
            raise ValueError(
                f"Line {self.line_number} (account {self.account_code}): "
                f"A line must have either a non-zero debit or a non-zero credit."
            )
        return self

    @property
    def is_debit(self) -> bool:
        return self.debit > 0

    @property
    def is_credit(self) -> bool:
        return self.credit > 0

    @property
    def amount(self) -> Decimal:
        """Signed amount: positive = debit, negative = credit."""
        return self.debit - self.credit


class JournalEntry(BaseModel):
    """
    A complete double-entry journal entry as submitted by a finance team member.

    This is the raw, unmodified representation of one object from
    manual_adjustments.json. It is NEVER mutated by the pipeline — a snapshot
    is stored verbatim in the audit log for traceability.
    """
    entry_id: str = Field(
        ...,
        description="Unique business identifier, e.g. 'JE-001'. Used for duplicate checks.",
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9\-_]+$",
    )
    description: str = Field(
        ...,
        description="Human-readable purpose of the entry. Fed to LLM for narrative analysis.",
        min_length=1,
        max_length=1000,
    )
    entry_date: date = Field(
        ...,
        description="Accounting date of the entry (ISO 8601: YYYY-MM-DD).",
    )
    lines: List[JournalLine] = Field(
        ...,
        description="The debit/credit lines. Must have at least 2 (one debit, one credit).",
        min_length=2,
    )
    prepared_by: Optional[str] = Field(
        default=None,
        description="User ID or name of the person who created the entry.",
        max_length=100,
    )
    approved_by: Optional[str] = Field(
        default=None,
        description="User ID or name of the approver. May be None for unapproved entries.",
        max_length=100,
    )
    reference: Optional[str] = Field(
        default=None,
        description="External reference number (PO, invoice, contract). Optional.",
        max_length=100,
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Free-form tags for filtering (e.g. ['Q1-close', 'intercompany']).",
    )
    raw_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any extra fields from the source system, preserved for auditability.",
    )

    @property
    def total_debits(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), Decimal("0.00"))

    @property
    def total_credits(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), Decimal("0.00"))

    @property
    def is_balanced(self) -> bool:
        return self.total_debits == self.total_credits

    @property
    def imbalance(self) -> Decimal:
        """Positive means debits exceed credits; negative means credits exceed debits."""
        return self.total_debits - self.total_credits


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 — VALIDATION PIPELINE MODELS
# ──────────────────────────────────────────────────────────────────────────────

class CheckResult(BaseModel):
    """
    The result of running one deterministic check against one JournalEntry.
    All checks run to completion (except SCHEMA which short-circuits the rest).
    """
    check: CheckName = Field(..., description="Which check was performed.")
    passed: bool = Field(..., description="True if the check found no violations.")
    error_code: Optional[ErrorCode] = Field(
        default=None,
        description="Populated only when passed=False.",
    )
    detail: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured detail about the failure, e.g. "
            "{'debit_total': '1500.00', 'credit_total': '1000.00', 'delta': '500.00'} "
            "for ERR_UNBALANCED. Passed to the LLM inside ValidatorHandoff."
        ),
    )


class ValidatorHandoff(BaseModel):
    """
    The typed contract passed from the Deterministic Validator to the LLM Explainer.

    The LLM ONLY receives this object — it never touches the COA file directly
    or calls any tool. This prevents hallucinated account lookups.
    """
    trace_id: str = Field(
        ...,
        description="Globally unique pipeline trace ID for this run of this entry.",
    )
    entry_id: str = Field(..., description="The business entry identifier.")
    raw_entry: JournalEntry = Field(
        ...,
        description=(
            "Unmodified copy of the original entry. "
            "The LLM uses this for context when writing the explanation."
        ),
    )
    error_codes: List[ErrorCode] = Field(
        ...,
        description="All error codes fired by the deterministic validator.",
        min_length=1,
    )
    error_details: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Merged detail dict from all failing CheckResults. "
            "Keys are error_code strings; values are the detail dicts."
        ),
    )
    checks_run: List[CheckResult] = Field(
        ...,
        description="Full list of all checks performed (pass and fail alike).",
    )
    coa_sample: List[str] = Field(
        default_factory=list,
        description=(
            "Up to 20 valid account codes from the COA, provided as context "
            "so the LLM can suggest corrections without hallucinating codes."
        ),
    )


class PlainEnglishExplanation(BaseModel):
    """
    Structured output produced BY the LLM Explainer and parsed via Pydantic.

    If the LLM response cannot be parsed into this model, the entry is
    QUARANTINED and llm_status is set to PARSE_FAILURE.
    """
    summary: str = Field(
        ...,
        description=(
            "One-sentence explanation for dashboard display. "
            "E.g.: 'Entry JE-002 rejected: debits exceed credits by $500.00.'"
        ),
        max_length=300,
    )
    detail: str = Field(
        ...,
        description=(
            "Full plain-English explanation for the finance team. "
            "Should name the specific lines, accounts, and amounts involved."
        ),
        max_length=2000,
    )
    severity: RiskSeverity = Field(
        ...,
        description="Risk severity: HIGH (data integrity), MEDIUM (policy), LOW (advisory).",
    )
    suggested_correction: Optional[str] = Field(
        default=None,
        description=(
            "Optional corrective action suggestion. Read-only — never auto-applied. "
            "If an account code is suggested, it must be re-validated against the COA."
        ),
        max_length=500,
    )
    risk_flags: List[str] = Field(
        default_factory=list,
        description=(
            "Semantic risk signals detected in the entry description or memos, "
            "e.g. ['prior-period reversal', 'round-number amount', 'end-of-quarter']."
        ),
    )
    llm_suggestion_verified: bool = Field(
        default=False,
        description=(
            "Set to True by the Orchestrator (not the LLM) if suggested_correction "
            "contains an account code that was re-validated against the COA."
        ),
    )


class ValidationResult(BaseModel):
    """
    The complete, final verdict for one JournalEntry.
    This is what gets written to validation_results.json for the frontend
    AND to validation_log.jsonl for auditors.
    """
    trace_id: str = Field(
        ...,
        description="Format: MAA-{YYYYMMDD}-{zero-padded sequence}, e.g. MAA-20260426-0001.",
    )
    entry_id: str
    status: EntryStatus
    error_codes: List[ErrorCode] = Field(default_factory=list)
    checks_run: List[CheckResult] = Field(default_factory=list)

    # LLM fields — populated only for REJECTED or QUARANTINED entries.
    llm_explanation: Optional[PlainEnglishExplanation] = None
    llm_status: LLMStatus = LLMStatus.NOT_CALLED
    llm_model: Optional[str] = Field(
        default=None,
        description="Model identifier used, e.g. 'gemini-2.0-flash'. Logged for auditability.",
    )
    llm_latency_ms: Optional[int] = Field(
        default=None,
        description="Round-trip latency of the LLM call in milliseconds.",
    )

    # Immutable snapshot of the raw input — never modified.
    raw_entry_snapshot: JournalEntry
    timestamp_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this result was finalised.",
    )
    pipeline_version: str = Field(
        default="1.0.0",
        description="Semver of the MAA pipeline that produced this result.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 5 — AUDIT LOG & RUN SUMMARY
# ──────────────────────────────────────────────────────────────────────────────

class AuditLogRecord(BaseModel):
    """
    One line in validation_log.jsonl (append-only).
    Structurally identical to ValidationResult but adds run_id linkage
    so auditors can group all records from the same pipeline invocation.
    """
    run_id: str = Field(
        ...,
        description="Identifier of the pipeline run that produced this record.",
    )
    # All ValidationResult fields are embedded directly.
    trace_id: str
    entry_id: str
    status: EntryStatus
    error_codes: List[ErrorCode] = Field(default_factory=list)
    checks_run: List[CheckResult] = Field(default_factory=list)
    llm_explanation: Optional[PlainEnglishExplanation] = None
    llm_status: LLMStatus = LLMStatus.NOT_CALLED
    llm_model: Optional[str] = None
    llm_latency_ms: Optional[int] = None
    raw_entry_snapshot: JournalEntry
    timestamp_utc: datetime
    pipeline_version: str = "1.0.0"

    @classmethod
    def from_validation_result(
        cls, result: ValidationResult, run_id: str
    ) -> "AuditLogRecord":
        """
        Convenience constructor: promote a ValidationResult into an AuditLogRecord
        by adding the run_id. Called by audit_logger.py.
        """
        return cls(run_id=run_id, **result.model_dump())


class PipelineRunSummary(BaseModel):
    """
    Written once per pipeline invocation to validation_results.json (top-level).
    Gives the frontend and operators a quick health snapshot of the run.
    """
    run_id: str = Field(
        ...,
        description="Unique identifier for this pipeline invocation, e.g. RUN-20260426-001.",
    )
    pipeline_version: str = "1.0.0"
    started_at: datetime
    completed_at: Optional[datetime] = None

    # Counts
    total_entries: int = 0
    accepted: int = 0
    rejected: int = 0
    quarantined: int = 0

    # COA preflight
    coa_loaded: bool = False
    coa_account_count: int = 0
    ic_check_active: bool = False
    ic_check_skipped_reason: Optional[str] = Field(
        default=None,
        description=(
            "If IC check was skipped (e.g., missing 'is_intercompany' column), "
            "the reason is recorded here. Always None when ic_check_active=True."
        ),
    )

    # Warnings accumulated during the run (non-fatal).
    warnings: List[str] = Field(default_factory=list)

    # All individual results — serialised into validation_results.json.
    results: List[ValidationResult] = Field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        if self.total_entries == 0:
            return 0.0
        return round(self.accepted / self.total_entries * 100, 2)

    @property
    def has_quarantined_entries(self) -> bool:
        return self.quarantined > 0
