"""
validator.py — Deterministic Validation Engine
================================================
Pure Python. Absolutely zero LLM calls. All arithmetic, rule-based, and
structural checks live in this module.

Design rules enforced here:
  - Never mutate the incoming JournalEntry.
  - All checks run to completion EXCEPT _check_schema, which short-circuits
    the remaining checks (a malformed entry cannot be balance-checked).
  - Amounts are compared as Decimal to avoid IEEE 754 floating-point drift.
  - The only output types are CheckResult (pass or fail) and ValidatorHandoff
    (the typed contract passed to the LLM Explainer on failure).

Public API:
    load_coa(csv_path)                                 → COAIndex
    validate_entry(entry, coa_index, seen_ids, run_warnings) → ValidatorOutput
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from backend.models import (
    COAAccount,
    CheckName,
    CheckResult,
    ErrorCode,
    JournalEntry,
    JournalLine,
    ValidatorHandoff,
)

# ──────────────────────────────────────────────────────────────────────────────
# TYPES
# ──────────────────────────────────────────────────────────────────────────────

# The in-memory COA index: account_code → COAAccount (O(1) lookup).
COAIndex = Dict[str, COAAccount]

# Return type of validate_entry:
#   checks_run     — full list of CheckResults (for audit log)
#   handoff        — populated only when one or more checks failed;
#                    None when the entry passed all checks.
ValidatorOutput = Tuple[List[CheckResult], Optional[ValidatorHandoff]]

log = logging.getLogger("maa.validator")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 — COA LOADER
# ──────────────────────────────────────────────────────────────────────────────

class COALoadError(RuntimeError):
    """Raised by load_coa() when the CSV is missing or fatally malformed.
    The Orchestrator catches this and halts the pipeline with STARTUP_FAILURE.
    """


def load_coa(csv_path: str | Path, run_warnings: Optional[List[str]] = None) -> COAIndex:
    """
    Parse chart_of_accounts.csv into an in-memory COAIndex.

    Expected CSV columns (case-insensitive headers):
        account_code, account_name, account_type,
        is_intercompany, is_active, normal_balance, parent_code

    Args:
        csv_path:     Absolute or relative path to the CSV file.
        run_warnings: Mutable list; non-fatal warnings are appended here.

    Returns:
        COAIndex — Dict[str, COAAccount], keyed by account_code.

    Raises:
        COALoadError — if the file is missing, empty, or lacks required columns.
    """
    path = Path(csv_path)
    if run_warnings is None:
        run_warnings = []

    if not path.exists():
        raise COALoadError(f"COA file not found: {path.resolve()}")
    if path.stat().st_size == 0:
        raise COALoadError(f"COA file is empty: {path.resolve()}")

    required_cols = {"account_code", "account_name", "account_type", "normal_balance"}
    optional_cols = {"is_intercompany", "is_active", "parent_code"}
    index: COAIndex = {}
    skipped = 0

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        # Normalise header names to lowercase for robustness.
        if reader.fieldnames is None:
            raise COALoadError("COA CSV has no header row.")
        headers = {h.strip().lower() for h in reader.fieldnames}

        # Check required columns exist.
        missing = required_cols - headers
        if missing:
            raise COALoadError(
                f"COA CSV is missing required columns: {sorted(missing)}. "
                f"Found: {sorted(headers)}"
            )

        # Warn if IC column is absent — IC circularity check will be skipped.
        if "is_intercompany" not in headers:
            msg = (
                "WARNING_IC_CHECK_SKIPPED: 'is_intercompany' column absent from COA. "
                "ERR_IC_CIRCULAR detection is disabled for this run."
            )
            run_warnings.append(msg)
            log.warning(msg)

        for row_num, raw_row in enumerate(reader, start=2):  # row 1 = header
            # Normalise keys.
            row = {k.strip().lower(): v.strip() for k, v in raw_row.items()}

            # Fill optional missing fields with safe defaults.
            row.setdefault("is_intercompany", "false")
            row.setdefault("is_active", "true")
            row.setdefault("parent_code", "")

            # Coerce boolean strings.
            row["is_intercompany"] = row["is_intercompany"].lower() in ("true", "1", "yes")
            row["is_active"] = row["is_active"].lower() not in ("false", "0", "no")

            # Empty parent_code → None.
            row["parent_code"] = row["parent_code"] or None

            try:
                account = COAAccount(**row)
                if account.account_code in index:
                    run_warnings.append(
                        f"Row {row_num}: duplicate account_code '{account.account_code}' — "
                        f"keeping first occurrence."
                    )
                    skipped += 1
                    continue
                index[account.account_code] = account
            except ValidationError as exc:
                run_warnings.append(
                    f"Row {row_num}: skipped due to schema error — {exc.error_count()} error(s). "
                    f"Raw row: {row}"
                )
                skipped += 1

    if not index:
        raise COALoadError("COA file parsed but produced zero valid accounts.")

    log.info(
        "COA loaded: %d accounts (%d skipped). IC check active: %s",
        len(index),
        skipped,
        "is_intercompany" in headers,
    )
    return index


def coa_has_ic_support(coa_index: COAIndex) -> bool:
    """
    Returns True if at least one account in the COA has is_intercompany=True.
    Used by the Orchestrator to decide whether to run IC circularity checks.
    """
    return any(acct.is_intercompany for acct in coa_index.values())


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 — INDIVIDUAL CHECK FUNCTIONS
# Each returns exactly one CheckResult. All are pure functions (no side effects).
# ──────────────────────────────────────────────────────────────────────────────

def _check_schema(entry: JournalEntry) -> CheckResult:
    """
    CHECK: SCHEMA
    Validates structural integrity of the entry using Pydantic model re-parse.

    The JournalEntry has already been parsed by the Orchestrator. This check
    acts as a secondary gate to catch edge cases that may have slipped through
    (e.g., lines list unexpectedly empty after initial parse).

    Short-circuits: YES — if this fails, balance/COA/IC checks are skipped.
    """
    issues: List[str] = []

    # Guard: must have at least 2 lines (one debit, one credit minimum).
    if len(entry.lines) < 2:
        issues.append(
            f"Entry has only {len(entry.lines)} line(s); minimum 2 required "
            f"(one debit, one credit)."
        )

    # Guard: description must not be blank or whitespace-only.
    if not entry.description.strip():
        issues.append("Entry description is blank or whitespace-only.")

    # Guard: check line_numbers are unique within the entry.
    line_nums = [ln.line_number for ln in entry.lines]
    duplicates = {n for n in line_nums if line_nums.count(n) > 1}
    if duplicates:
        issues.append(f"Duplicate line_numbers detected: {sorted(duplicates)}.")

    if issues:
        return CheckResult(
            check=CheckName.SCHEMA,
            passed=False,
            error_code=ErrorCode.ERR_SCHEMA_INVALID,
            detail={"issues": issues, "line_count": len(entry.lines)},
        )

    return CheckResult(check=CheckName.SCHEMA, passed=True, detail={})


def _check_balance(entry: JournalEntry) -> CheckResult:
    """
    CHECK: BALANCE
    Verifies sum(debits) == sum(credits) to exactly 2 decimal places.

    Uses Decimal arithmetic throughout — never float — to prevent
    representation errors (e.g., 0.1 + 0.2 != 0.3 in IEEE 754).
    """
    total_debit = entry.total_debits
    total_credit = entry.total_credits
    delta = entry.imbalance  # positive = debits exceed credits

    if delta != Decimal("0.00"):
        direction = "debits exceed credits" if delta > 0 else "credits exceed debits"
        return CheckResult(
            check=CheckName.BALANCE,
            passed=False,
            error_code=ErrorCode.ERR_UNBALANCED,
            detail={
                "debit_total": str(total_debit),
                "credit_total": str(total_credit),
                "delta": str(abs(delta)),
                "direction": direction,
                "debit_lines": [
                    {"line": ln.line_number, "account": ln.account_code, "amount": str(ln.debit)}
                    for ln in entry.lines if ln.is_debit
                ],
                "credit_lines": [
                    {"line": ln.line_number, "account": ln.account_code, "amount": str(ln.credit)}
                    for ln in entry.lines if ln.is_credit
                ],
            },
        )

    return CheckResult(
        check=CheckName.BALANCE,
        passed=True,
        detail={"debit_total": str(total_debit), "credit_total": str(total_credit)},
    )


def _check_coa_lookup(entry: JournalEntry, coa_index: COAIndex) -> CheckResult:
    """
    CHECK: COA_LOOKUP
    Every account_code in every line must exist in the COA and be active.

    Failures are collected for all lines (not short-circuited per line)
    so the LLM Explainer can mention all invalid codes in one explanation.
    """
    invalid: List[Dict] = []   # account codes not found in COA
    inactive: List[Dict] = []  # account codes found but marked is_active=False

    for line in entry.lines:
        acct = coa_index.get(line.account_code)
        if acct is None:
            invalid.append({
                "line_number": line.line_number,
                "account_code": line.account_code,
                "memo": line.memo,
            })
        elif not acct.is_active:
            inactive.append({
                "line_number": line.line_number,
                "account_code": line.account_code,
                "account_name": acct.account_name,
                "memo": line.memo,
            })

    if invalid or inactive:
        # Provide a sample of valid codes to give context to the LLM.
        valid_sample = sorted(coa_index.keys())[:20]
        return CheckResult(
            check=CheckName.COA_LOOKUP,
            passed=False,
            error_code=ErrorCode.ERR_INVALID_ACCOUNT,
            detail={
                "invalid_codes": invalid,
                "inactive_codes": inactive,
                "valid_account_sample": valid_sample,
                "total_coa_accounts": len(coa_index),
            },
        )

    return CheckResult(
        check=CheckName.COA_LOOKUP,
        passed=True,
        detail={"all_accounts_valid": True, "account_count": len(entry.lines)},
    )


def _check_ic_circular(entry: JournalEntry, coa_index: COAIndex) -> CheckResult:
    """
    CHECK: IC_CIRCULAR
    Detects entries where the same intercompany account code appears on
    BOTH the debit side and the credit side of the same entry.

    This is a hallmark of a circular intercompany posting that would
    net to zero and produce no economic substance — a common fraud indicator
    and accounting error.

    If the COA has no is_intercompany=True accounts, this check passes
    trivially (the Orchestrator will log WARNING_IC_CHECK_SKIPPED separately).
    """
    # Build sets of IC accounts on each side.
    ic_debits: Set[str] = set()
    ic_credits: Set[str] = set()

    for line in entry.lines:
        acct = coa_index.get(line.account_code)
        if acct and acct.is_intercompany:
            if line.is_debit:
                ic_debits.add(line.account_code)
            elif line.is_credit:
                ic_credits.add(line.account_code)

    # Circular = same IC account on both sides.
    circular = ic_debits & ic_credits

    if circular:
        circular_details = []
        for code in sorted(circular):
            acct = coa_index[code]
            debit_lines = [
                {"line": ln.line_number, "amount": str(ln.debit)}
                for ln in entry.lines
                if ln.account_code == code and ln.is_debit
            ]
            credit_lines = [
                {"line": ln.line_number, "amount": str(ln.credit)}
                for ln in entry.lines
                if ln.account_code == code and ln.is_credit
            ]
            circular_details.append({
                "account_code": code,
                "account_name": acct.account_name,
                "debit_lines": debit_lines,
                "credit_lines": credit_lines,
            })

        return CheckResult(
            check=CheckName.IC_CIRCULAR,
            passed=False,
            error_code=ErrorCode.ERR_IC_CIRCULAR,
            detail={
                "circular_accounts": circular_details,
                "explanation_hint": (
                    "An intercompany account that debits and credits within the same "
                    "journal entry nets to zero and has no economic effect. This is a "
                    "common sign of a data entry error or a prohibited circular booking."
                ),
            },
        )

    return CheckResult(
        check=CheckName.IC_CIRCULAR,
        passed=True,
        detail={
            "ic_debit_accounts": sorted(ic_debits),
            "ic_credit_accounts": sorted(ic_credits),
        },
    )


def _check_duplicate_id(entry: JournalEntry, seen_ids: Set[str]) -> CheckResult:
    """
    CHECK: DUPLICATE_ID
    Ensures entry_id has not already been processed in this pipeline run.

    The seen_ids set is maintained by the Orchestrator and passed by reference.
    This check does NOT add the ID to seen_ids — the Orchestrator does that
    after the check returns, ensuring correct sequencing.
    """
    if entry.entry_id in seen_ids:
        return CheckResult(
            check=CheckName.DUPLICATE_ID,
            passed=False,
            error_code=ErrorCode.ERR_DUPLICATE_ID,
            detail={
                "entry_id": entry.entry_id,
                "message": (
                    f"entry_id '{entry.entry_id}' has already been processed in this run. "
                    f"Resubmissions must use a new entry_id."
                ),
            },
        )

    return CheckResult(
        check=CheckName.DUPLICATE_ID,
        passed=True,
        detail={"entry_id": entry.entry_id},
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 — HANDOFF BUILDER
# Constructs the ValidatorHandoff passed to the LLM Explainer on failure.
# ──────────────────────────────────────────────────────────────────────────────

def _build_handoff(
    trace_id: str,
    entry: JournalEntry,
    checks_run: List[CheckResult],
    coa_index: COAIndex,
) -> ValidatorHandoff:
    """
    Consolidate all failing CheckResults into a single ValidatorHandoff.

    The error_details dict merges the detail payloads from every failing check,
    keyed by error_code string. This gives the LLM a single rich context object.

    coa_sample: provides up to 20 valid account codes sorted alphabetically so
    the LLM can suggest plausible corrections without hallucinating codes.
    """
    failed = [c for c in checks_run if not c.passed]
    error_codes = [c.error_code for c in failed if c.error_code]

    # Merge detail dicts, keyed by error code string.
    error_details: Dict = {}
    for check in failed:
        if check.error_code:
            error_details[check.error_code.value] = check.detail

    # Provide a sample of valid codes as guardrail for the LLM.
    coa_sample = sorted(coa_index.keys())[:20]

    return ValidatorHandoff(
        trace_id=trace_id,
        entry_id=entry.entry_id,
        raw_entry=entry,
        error_codes=error_codes,
        error_details=error_details,
        checks_run=checks_run,
        coa_sample=coa_sample,
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 — MAIN VALIDATION ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def validate_entry(
    entry: JournalEntry,
    coa_index: COAIndex,
    seen_ids: Set[str],
    trace_id: str,
    ic_check_active: bool = True,
) -> ValidatorOutput:
    """
    Run the full deterministic validation suite against a single JournalEntry.

    Check execution order:
        1. DUPLICATE_ID  — checked first so we catch dupes before any other work.
        2. SCHEMA        — short-circuits if structural issues are found.
        3. BALANCE       — arithmetic check (Decimal, not float).
        4. COA_LOOKUP    — all account codes must exist and be active.
        5. IC_CIRCULAR   — same IC account on both sides (if ic_check_active).

    Args:
        entry:           Parsed JournalEntry (not mutated).
        coa_index:       Loaded COA dict from load_coa().
        seen_ids:        Mutable set of already-processed entry_ids (read-only here).
        trace_id:        Pre-assigned unique trace ID for this entry.
        ic_check_active: Set False if COA lacks is_intercompany column.

    Returns:
        (checks_run, None)          if all checks passed.
        (checks_run, ValidatorHandoff) if one or more checks failed.
    """
    checks_run: List[CheckResult] = []

    log.info("[%s] Validating entry '%s'", trace_id, entry.entry_id)

    # ── CHECK 1: Duplicate ID ──────────────────────────────────────────────────
    dup_result = _check_duplicate_id(entry, seen_ids)
    checks_run.append(dup_result)
    if not dup_result.passed:
        # A duplicate cannot be meaningfully validated further.
        log.warning("[%s] DUPLICATE_ID: '%s'", trace_id, entry.entry_id)
        return checks_run, _build_handoff(trace_id, entry, checks_run, coa_index)

    # ── CHECK 2: Schema ───────────────────────────────────────────────────────
    schema_result = _check_schema(entry)
    checks_run.append(schema_result)
    if not schema_result.passed:
        # Schema failures mean the entry is structurally broken.
        # Skip arithmetic/semantic checks — they would produce misleading output.
        log.warning("[%s] ERR_SCHEMA_INVALID: %s", trace_id, schema_result.detail)
        return checks_run, _build_handoff(trace_id, entry, checks_run, coa_index)

    # ── CHECK 3: Balance ──────────────────────────────────────────────────────
    balance_result = _check_balance(entry)
    checks_run.append(balance_result)
    if not balance_result.passed:
        log.warning(
            "[%s] ERR_UNBALANCED: debits=%s credits=%s delta=%s",
            trace_id,
            balance_result.detail.get("debit_total"),
            balance_result.detail.get("credit_total"),
            balance_result.detail.get("delta"),
        )

    # ── CHECK 4: COA Lookup ───────────────────────────────────────────────────
    coa_result = _check_coa_lookup(entry, coa_index)
    checks_run.append(coa_result)
    if not coa_result.passed:
        invalid = coa_result.detail.get("invalid_codes", [])
        inactive = coa_result.detail.get("inactive_codes", [])
        log.warning(
            "[%s] ERR_INVALID_ACCOUNT: %d invalid, %d inactive",
            trace_id, len(invalid), len(inactive),
        )

    # ── CHECK 5: IC Circularity ───────────────────────────────────────────────
    if ic_check_active:
        ic_result = _check_ic_circular(entry, coa_index)
        checks_run.append(ic_result)
        if not ic_result.passed:
            log.warning(
                "[%s] ERR_IC_CIRCULAR: accounts=%s",
                trace_id,
                [d["account_code"] for d in ic_result.detail.get("circular_accounts", [])],
            )
    else:
        log.debug("[%s] IC_CIRCULAR check skipped (ic_check_active=False)", trace_id)

    # ── DECISION ──────────────────────────────────────────────────────────────
    any_failed = any(not c.passed for c in checks_run)

    if any_failed:
        return checks_run, _build_handoff(trace_id, entry, checks_run, coa_index)

    log.info("[%s] Entry '%s' PASSED all checks.", trace_id, entry.entry_id)
    return checks_run, None  # None handoff = ACCEPTED
