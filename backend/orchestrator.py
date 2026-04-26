"""
orchestrator.py — Pipeline State Machine
==========================================
The single entry point that:
  1. Loads the COA and journal entries.
  2. Assigns trace_ids.
  3. Routes each entry through Validator → (LLM Explainer on failure).
  4. Writes audit logs and final results.

There is NO multi-agent loop. This is a straight pipeline:
    INPUT → VALIDATE → (EXPLAIN if failed) → LOG → OUTPUT
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import ValidationError

from backend.models import (
    EntryStatus,
    ErrorCode,
    JournalEntry,
    LLMStatus,
    PipelineRunSummary,
    ValidationResult,
)
from backend.validator import (
    COAIndex,
    COALoadError,
    coa_has_ic_support,
    load_coa,
    validate_entry,
)
from backend.llm_explainer import explain_errors
from backend.audit_logger import AuditLogger

log = logging.getLogger("maa.orchestrator")


# ──────────────────────────────────────────────────────────────────────────────
# TRACE ID GENERATOR
# ──────────────────────────────────────────────────────────────────────────────

def _generate_trace_id(run_date: date, sequence: int) -> str:
    """
    Format: MAA-YYYYMMDD-NNNN (e.g., MAA-20260426-0003).
    Globally unique within a single pipeline run.
    """
    return f"MAA-{run_date.strftime('%Y%m%d')}-{sequence:04d}"


def _generate_run_id(run_date: date) -> str:
    """
    Format: RUN-YYYYMMDD-HHMMSS (e.g., RUN-20260426-083011).
    """
    now = datetime.now(timezone.utc)
    return f"RUN-{now.strftime('%Y%m%d-%H%M%S')}"


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY PARSER
# ──────────────────────────────────────────────────────────────────────────────

def _parse_entry(
    raw: Dict[str, Any],
    trace_id: str,
) -> tuple[Optional[JournalEntry], Optional[ValidationResult]]:
    """
    Attempt to parse a raw dict into a JournalEntry via Pydantic.

    Returns:
        (entry, None)                 on success.
        (None, ValidationResult)      on parse failure — entry is QUARANTINED
                                      with ERR_SCHEMA_INVALID because we can't
                                      even construct a valid JournalEntry.
    """
    # Strip internal metadata keys (e.g., "_scenario", "_comment").
    clean = {k: v for k, v in raw.items() if not k.startswith("_")}

    try:
        entry = JournalEntry(**clean)
        return entry, None
    except ValidationError as exc:
        # We couldn't even parse the entry. Produce a minimal QUARANTINED result.
        entry_id = raw.get("entry_id", "UNKNOWN")
        log.error(
            "[%s] Pydantic parse failure for entry_id='%s': %s",
            trace_id, entry_id, exc,
        )

        # Create a minimal snapshot for the audit log — raw data as-is.
        # We can't construct a proper JournalEntry, so we build a synthetic one.
        try:
            # Try to build a partial entry for the snapshot.
            fallback_entry = JournalEntry(
                entry_id=str(entry_id) if entry_id else "PARSE_FAILURE",
                description=str(raw.get("description", "Parse failure — no description")),
                entry_date=raw.get("entry_date", date.today().isoformat()),
                lines=[
                    {"line_number": 1, "account_code": "0000", "debit": 1, "credit": 0,
                     "memo": "Synthetic line — original entry failed Pydantic validation"},
                    {"line_number": 2, "account_code": "0000", "debit": 0, "credit": 1,
                     "memo": "Synthetic line — see raw_metadata for original data"},
                ],
                raw_metadata={"parse_errors": str(exc), "original_raw": raw},
            )
        except Exception:
            # Absolute fallback — should never happen but belt-and-suspenders.
            fallback_entry = JournalEntry(
                entry_id="PARSE_FAILURE",
                description="Entry could not be parsed at all",
                entry_date=date.today(),
                lines=[
                    {"line_number": 1, "account_code": "0000", "debit": 1, "credit": 0},
                    {"line_number": 2, "account_code": "0000", "debit": 0, "credit": 1},
                ],
                raw_metadata={"original_raw": str(raw)[:2000]},
            )

        from backend.models import CheckName, CheckResult
        result = ValidationResult(
            trace_id=trace_id,
            entry_id=str(entry_id),
            status=EntryStatus.QUARANTINED,
            error_codes=[ErrorCode.ERR_SCHEMA_INVALID],
            checks_run=[
                CheckResult(
                    check=CheckName.SCHEMA,
                    passed=False,
                    error_code=ErrorCode.ERR_SCHEMA_INVALID,
                    detail={
                        "parse_error": str(exc),
                        "entry_id": str(entry_id),
                    },
                )
            ],
            llm_status=LLMStatus.NOT_CALLED,
            raw_entry_snapshot=fallback_entry,
        )
        return None, result


# ──────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    adjustments_path: str = "data/manual_adjustments.json",
    coa_path: str = "data/chart_of_accounts.csv",
    output_results_path: str = "output/validation_results.json",
    output_log_path: str = "output/validation_log.jsonl",
    llm_provider: Optional[str] = None,
    llm_timeout: int = 10,
    skip_llm: bool = False,
) -> PipelineRunSummary:
    """
    Execute the full Manual Adjustments Agent pipeline.

    Steps:
        1. Preflight: load COA, load adjustments JSON.
        2. For each entry:
           a. Parse via Pydantic (ERR_SCHEMA_INVALID on failure).
           b. Run deterministic validator (5 checks).
           c. If any check failed and LLM is enabled: call LLM Explainer.
           d. Determine final status: ACCEPTED / REJECTED / QUARANTINED.
           e. Write audit log record.
        3. Write validation_results.json with all results.
        4. Return PipelineRunSummary.
    """
    started_at = datetime.now(timezone.utc)
    run_date = started_at.date()
    run_id = _generate_run_id(run_date)
    run_warnings: List[str] = []

    log.info("=" * 60)
    log.info("MAA Pipeline starting — run_id=%s", run_id)
    log.info("=" * 60)

    # ── PREFLIGHT: Load COA ───────────────────────────────────────────────
    try:
        coa_index = load_coa(coa_path, run_warnings)
        ic_active = coa_has_ic_support(coa_index)
        coa_loaded = True
        coa_count = len(coa_index)
        log.info("COA loaded: %d accounts, IC check: %s", coa_count, ic_active)
    except COALoadError as exc:
        log.critical("STARTUP_FAILURE: %s", exc)
        return PipelineRunSummary(
            run_id=run_id,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            coa_loaded=False,
            ic_check_active=False,
            ic_check_skipped_reason=str(exc),
            warnings=[f"STARTUP_FAILURE: {exc}"],
        )

    if not ic_active:
        reason = "No accounts with is_intercompany=True found in COA."
        run_warnings.append(f"WARNING_IC_CHECK_SKIPPED: {reason}")
        log.warning(reason)

    # ── PREFLIGHT: Load Adjustments JSON ──────────────────────────────────
    adj_path = Path(adjustments_path)
    if not adj_path.exists():
        msg = f"Adjustments file not found: {adj_path.resolve()}"
        log.critical(msg)
        return PipelineRunSummary(
            run_id=run_id,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            coa_loaded=coa_loaded,
            coa_account_count=coa_count,
            warnings=[f"STARTUP_FAILURE: {msg}"],
        )

    with open(adj_path, encoding="utf-8") as f:
        raw_data = json.load(f)

    entries_raw: List[Dict] = raw_data.get("entries", [])
    total = len(entries_raw)
    log.info("Loaded %d raw entries from %s", total, adj_path)

    # ── INIT: Audit logger + tracking ─────────────────────────────────────
    audit = AuditLogger(output_log_path, run_id=run_id)
    seen_ids: Set[str] = set()
    results: List[ValidationResult] = []
    coa_valid_codes = set(coa_index.keys())
    sequence = 0

    # ── MAIN LOOP ─────────────────────────────────────────────────────────
    for idx, raw_entry in enumerate(entries_raw):
        sequence += 1
        trace_id = _generate_trace_id(run_date, sequence)
        entry_id = raw_entry.get("entry_id", f"UNKNOWN-{idx}")

        log.info(
            "─── Processing %d/%d: entry_id='%s' trace_id='%s' ───",
            idx + 1, total, entry_id, trace_id,
        )

        # ── STEP A: Parse ─────────────────────────────────────────────────
        entry, parse_failure = _parse_entry(raw_entry, trace_id)

        if parse_failure is not None:
            # Entry couldn't even be parsed — QUARANTINED immediately.
            results.append(parse_failure)
            audit.write(parse_failure)
            log.warning(
                "[%s] Entry '%s' QUARANTINED at parse stage.", trace_id, entry_id,
            )
            continue

        # ── STEP B: Validate (deterministic) ──────────────────────────────
        checks_run, handoff = validate_entry(
            entry=entry,
            coa_index=coa_index,
            seen_ids=seen_ids,
            trace_id=trace_id,
            ic_check_active=ic_active,
        )

        # Register entry_id after validation (for duplicate detection).
        seen_ids.add(entry.entry_id)

        # ── STEP C: Route by validation outcome ──────────────────────────
        if handoff is None:
            # ALL CHECKS PASSED → ACCEPTED. No LLM call.
            result = ValidationResult(
                trace_id=trace_id,
                entry_id=entry.entry_id,
                status=EntryStatus.ACCEPTED,
                error_codes=[],
                checks_run=checks_run,
                llm_status=LLMStatus.NOT_CALLED,
                raw_entry_snapshot=entry,
            )
            results.append(result)
            audit.write(result)
            log.info("[%s] Entry '%s' → ACCEPTED", trace_id, entry.entry_id)
            continue

        # ── STEP D: Call LLM Explainer (only on failure) ──────────────────
        if skip_llm:
            # LLM disabled — QUARANTINE with no explanation.
            result = ValidationResult(
                trace_id=trace_id,
                entry_id=entry.entry_id,
                status=EntryStatus.QUARANTINED,
                error_codes=[c.error_code for c in checks_run if not c.passed and c.error_code],
                checks_run=checks_run,
                llm_status=LLMStatus.SKIPPED,
                raw_entry_snapshot=entry,
            )
            results.append(result)
            audit.write(result)
            log.info("[%s] Entry '%s' → QUARANTINED (LLM skipped)", trace_id, entry.entry_id)
            continue

        explanation, llm_status, model_name, latency_ms = explain_errors(
            handoff=handoff,
            coa_valid_codes=coa_valid_codes,
            provider=llm_provider,
            timeout=llm_timeout,
        )

        if llm_status == LLMStatus.OK and explanation is not None:
            # LLM succeeded → REJECTED with explanation.
            status = EntryStatus.REJECTED
        else:
            # LLM failed → QUARANTINED for human review.
            status = EntryStatus.QUARANTINED

        result = ValidationResult(
            trace_id=trace_id,
            entry_id=entry.entry_id,
            status=status,
            error_codes=[c.error_code for c in checks_run if not c.passed and c.error_code],
            checks_run=checks_run,
            llm_explanation=explanation,
            llm_status=llm_status,
            llm_model=model_name,
            llm_latency_ms=latency_ms,
            raw_entry_snapshot=entry,
        )
        results.append(result)
        audit.write(result)
        log.info(
            "[%s] Entry '%s' → %s (llm=%s, %s ms)",
            trace_id, entry.entry_id, status.value,
            llm_status.value, latency_ms,
        )

    # ── WRITE FINAL OUTPUT ────────────────────────────────────────────────
    completed_at = datetime.now(timezone.utc)

    summary = PipelineRunSummary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        total_entries=total,
        accepted=sum(1 for r in results if r.status == EntryStatus.ACCEPTED),
        rejected=sum(1 for r in results if r.status == EntryStatus.REJECTED),
        quarantined=sum(1 for r in results if r.status == EntryStatus.QUARANTINED),
        coa_loaded=coa_loaded,
        coa_account_count=coa_count,
        ic_check_active=ic_active,
        ic_check_skipped_reason=None if ic_active else "No IC accounts in COA or column missing.",
        warnings=run_warnings,
        results=results,
    )

    # Write validation_results.json.
    output_path = Path(output_results_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(summary.model_dump_json(indent=2))

    log.info("=" * 60)
    log.info("Pipeline complete — run_id=%s", run_id)
    log.info(
        "  Accepted: %d | Rejected: %d | Quarantined: %d | Total: %d",
        summary.accepted, summary.rejected, summary.quarantined, summary.total_entries,
    )
    log.info("  Acceptance rate: %.1f%%", summary.acceptance_rate)
    log.info("  Results written to: %s", output_path.resolve())
    log.info("  Audit log: %s", Path(output_log_path).resolve())
    log.info("=" * 60)

    return summary
