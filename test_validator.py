"""
test_validator.py — Integration test for the deterministic validator.
Runs all 12 mock entries and prints a structured pass/fail report.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.validator import load_coa, validate_entry, coa_has_ic_support
from backend.models import JournalEntry

# ── Load fixtures ──────────────────────────────────────────────────────────
warnings: list = []
coa = load_coa("data/chart_of_accounts.csv", warnings)
ic_active = coa_has_ic_support(coa)

with open("data/manual_adjustments.json", encoding="utf-8") as f:
    raw = json.load(f)

entries_raw = raw["entries"]

EXPECTED = {
    ("JE-001", 0): set(),
    ("JE-002", 0): {"ERR_UNBALANCED"},
    ("JE-003", 0): set(),
    ("JE-004", 0): set(),
    ("JE-005", 0): {"ERR_INVALID_ACCOUNT"},
    ("JE-006", 0): set(),
    ("JE-007", 0): set(),
    ("JE-008", 0): {"ERR_IC_CIRCULAR"},
    ("JE-009", 0): set(),
    ("JE-010", 0): set(),
}

seen_ids: set = set()
trace_counter = 0
pass_count = 0
fail_count = 0
id_occurrence: dict = {}

print(f"\n{'─'*72}")
print(f"  MAA Deterministic Validator — Test Suite")
print(f"  COA: {len(coa)} accounts | IC checks: {'ON' if ic_active else 'OFF'}")
print(f"{'─'*72}\n")

for raw_entry in entries_raw:
    entry_id = raw_entry.get("entry_id", "UNKNOWN")
    occurrence = id_occurrence.get(entry_id, 0)
    id_occurrence[entry_id] = occurrence + 1
    trace_counter += 1
    trace_id = f"MAA-TEST-{trace_counter:04d}"

    # JE-009 has a schema violation at the Pydantic level — handle gracefully.
    try:
        entry = JournalEntry(**{k: v for k, v in raw_entry.items() if not k.startswith("_")})
        pydantic_ok = True
    except Exception as exc:
        pydantic_ok = False
        pydantic_error = str(exc)

    key = (entry_id, occurrence)
    expected_codes = EXPECTED.get(key, None)

    if not pydantic_ok:
        # Schema errors caught at Pydantic parse time.
        got_codes = {"ERR_SCHEMA_INVALID"}
        status = "✅ PASS" if got_codes == expected_codes else "❌ FAIL"
        if expected_codes == got_codes:
            pass_count += 1
        else:
            fail_count += 1
        print(f"  {status} | {trace_id} | {entry_id}[{occurrence}]")
        print(f"         Got (pydantic): {sorted(got_codes)}")
        print(f"         Expected:       {sorted(expected_codes or [])}\n")
        continue

    checks_run, handoff = validate_entry(
        entry=entry,
        coa_index=coa,
        seen_ids=seen_ids,
        trace_id=trace_id,
        ic_check_active=ic_active,
    )

    # Register the ID after first successful parse.
    seen_ids.add(entry_id)  # always add, same as orchestrator.py

    got_codes = {c.error_code.value for c in checks_run if not c.passed and c.error_code}
    match = got_codes == (expected_codes or set())
    status = "✅ PASS" if match else "❌ FAIL"

    if match:
        pass_count += 1
    else:
        fail_count += 1

    checks_summary = ", ".join(
        f"{c.check.value}:{'✓' if c.passed else '✗'}" for c in checks_run
    )
    print(f"  {status} | {trace_id} | {entry_id}[{occurrence}]")
    if got_codes:
        print(f"         Errors:   {sorted(got_codes)}")
    if not match:
        print(f"         Expected: {sorted(expected_codes or [])}")
    print(f"         Checks:   {checks_summary}\n")

print(f"{'─'*72}")
print(f"  Results: {pass_count} passed, {fail_count} failed out of 10 entries")
if warnings:
    print(f"  Warnings ({len(warnings)}):")
    for w in warnings:
        print(f"    ⚠ {w}")
print(f"{'─'*72}\n")

sys.exit(0 if fail_count == 0 else 1)
