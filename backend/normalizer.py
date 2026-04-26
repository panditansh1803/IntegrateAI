"""
normalizer.py — Company Input Adapter
=======================================
Reads ALL files from the /inputs folder (company-provided, intentionally messy)
and writes cleaned, schema-compatible versions to /data/ for the pipeline.

Input files consumed:
  inputs/chart_of_accounts.csv     → data/chart_of_accounts.csv
  inputs/manual_adjustments.json   → data/manual_adjustments.json
  inputs/trial_balance.csv         → data/trial_balance_normalized.csv
  inputs/prior_period_tb.csv       → data/prior_period_tb_normalized.csv
  inputs/fx_rates.csv              → data/fx_rates_normalized.csv

CRITICAL RULE: This normalizer NEVER changes economic values (amounts,
account codes, descriptions). It only remaps field names, infers missing
flags, strips Header rows, and translates multi-currency amounts to USD.
All defects in the source data are PRESERVED and passed to the validator.
"""

from __future__ import annotations

import csv
import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("maa.normalizer")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 — FX RATES LOADER
# Load rates first — needed for TB translation.
# ──────────────────────────────────────────────────────────────────────────────

def load_fx_rates(fx_path: str | Path) -> Dict[str, Dict[str, Decimal]]:
    """
    Load FX rates from inputs/fx_rates.csv.

    Returns: { currency: { rate_type: Decimal } }
    e.g. { "EUR": { "period_end": Decimal("1.095"), "period_average": Decimal("1.082") } }
    USD always maps to 1.0 for all rate types.
    """
    path = Path(fx_path)
    rates: Dict[str, Dict[str, Decimal]] = {}

    if not path.exists():
        log.warning("FX rates file not found: %s. Assuming all 1:1.", path)
        return {"USD": {"period_average": Decimal("1.0"), "period_end": Decimal("1.0")}}

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ccy = row["currency"].strip().upper()
            rate_type = row["rate_type"].strip().lower()
            try:
                rate = Decimal(str(row["rate"]).strip())
            except Exception:
                log.warning("Could not parse FX rate for %s/%s: %s", ccy, rate_type, row["rate"])
                continue
            if ccy not in rates:
                rates[ccy] = {}
            rates[ccy][rate_type] = rate

    # Ensure USD always present.
    rates.setdefault("USD", {})["period_average"] = Decimal("1.0")
    rates.setdefault("USD", {})["period_end"] = Decimal("1.0")
    rates.setdefault("USD", {})["opening"] = Decimal("1.0")

    log.info("FX rates loaded: %d currencies", len(rates))
    return rates


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 — COA NORMALIZER
# ──────────────────────────────────────────────────────────────────────────────

IC_KEYWORDS = {"intercompany", "inter-company", "ic payable", "ic receivable", "i/c"}

TYPE_MAP = {
    "asset": "ASSET",
    "liability": "LIABILITY",
    "equity": "EQUITY",
    "revenue": "REVENUE",
    "expense": "EXPENSE",
}

def normalize_coa(
    input_path: str | Path,
    output_path: str | Path,
    warnings: List[str],
) -> int:
    """
    Normalize company COA CSV → pipeline-compatible COA CSV.

    Key transforms:
    - "Header" account_type rows → SKIPPED (not postable accounts)
    - Mixed-case normal_balance → uppercase DEBIT/CREDIT
    - Missing normal_balance → inferred from account_type
    - is_intercompany → inferred from account_name keywords
    - is_active → True by default
    - Extra columns (statement, cf_category) → preserved in output
    """
    path_in = Path(input_path)
    path_out = Path(output_path)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    if not path_in.exists():
        raise FileNotFoundError(f"COA not found: {path_in.resolve()}")

    output_fields = [
        "account_code", "account_name", "account_type",
        "is_intercompany", "is_active", "normal_balance", "parent_code",
        "statement", "cf_category",
    ]

    written = 0
    skipped_headers = 0

    with open(path_in, newline="", encoding="utf-8-sig") as fin:
        reader = csv.DictReader(fin)

        with open(path_out, "w", newline="", encoding="utf-8") as fout:
            writer = csv.DictWriter(fout, fieldnames=output_fields, extrasaction="ignore")
            writer.writeheader()

            for row_num, raw in enumerate(reader, start=2):
                row = {k.strip().lower(): v.strip() for k, v in raw.items()}
                raw_type = row.get("account_type", "").lower()

                # Skip Header rows — they are structural groupings, not ledger accounts.
                if raw_type == "header":
                    skipped_headers += 1
                    continue

                # Map account type.
                acct_type = TYPE_MAP.get(raw_type)
                if acct_type is None:
                    warnings.append(
                        f"COA row {row_num}: unknown account_type '{raw_type}' "
                        f"for account '{row.get('account_code')}' — SKIPPED."
                    )
                    continue

                # Normalise normal_balance.
                nb = row.get("normal_balance", "").strip().lower()
                if nb == "debit":
                    normal_balance = "DEBIT"
                elif nb == "credit":
                    normal_balance = "CREDIT"
                elif nb == "":
                    normal_balance = "DEBIT" if acct_type in ("ASSET", "EXPENSE") else "CREDIT"
                    warnings.append(
                        f"COA row {row_num}: missing normal_balance for "
                        f"'{row.get('account_code')}' — inferred '{normal_balance}'."
                    )
                else:
                    normal_balance = "DEBIT"
                    warnings.append(
                        f"COA row {row_num}: unrecognised normal_balance '{nb}' — defaulted to DEBIT."
                    )

                # Infer intercompany flag from account name.
                acct_name = row.get("account_name", "")
                is_ic = any(kw in acct_name.lower() for kw in IC_KEYWORDS)

                writer.writerow({
                    "account_code":   row.get("account_code", ""),
                    "account_name":   acct_name,
                    "account_type":   acct_type,
                    "is_intercompany": str(is_ic).lower(),
                    "is_active":      "true",
                    "normal_balance": normal_balance,
                    "parent_code":    row.get("parent_code", ""),
                    "statement":      row.get("statement", ""),
                    "cf_category":    row.get("cf_category", ""),
                })
                written += 1

    log.info("COA: %d accounts written, %d headers skipped → %s", written, skipped_headers, path_out)
    return written


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 — ADJUSTMENTS NORMALIZER
# Maps company JSON field names → pipeline JournalEntry field names.
# ──────────────────────────────────────────────────────────────────────────────

def normalize_adjustments(
    input_path: str | Path,
    output_path: str | Path,
    warnings: List[str],
) -> Tuple[int, List[str]]:
    """
    Normalize company manual_adjustments.json → pipeline-compatible JSON.

    Field mapping:
      "id"      → "entry_id"
      "date"    → "entry_date"
      "source"  → "prepared_by"
      lines[].account → lines[].account_code
      lines[] get auto-assigned line_number (1-indexed)

    Defects preserved (NOT fixed):
      - Unbalanced entries (JE-002: debits $28,500 ≠ credits $25,000)
      - Invalid account codes (JE-005: account 6315 not in COA)
      - IC circular entries (JE-008: account 2170 on both sides)
    """
    path_in = Path(input_path)
    path_out = Path(output_path)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    if not path_in.exists():
        raise FileNotFoundError(f"Adjustments not found: {path_in.resolve()}")

    with open(path_in, encoding="utf-8") as f:
        raw = json.load(f)

    entries_raw = raw.get("entries", [])
    defects_found: List[str] = []

    normalized_entries = []
    seen_ids: set = set()

    for idx, entry in enumerate(entries_raw):
        entry_id = entry.get("id", f"UNKNOWN-{idx}")

        # Pre-scan for known defects — logged as warnings but NOT fixed.
        if entry_id in seen_ids:
            msg = f"DEFECT: Duplicate entry id '{entry_id}' at index {idx}."
            warnings.append(msg)
            defects_found.append(msg)
        seen_ids.add(entry_id)

        raw_lines = entry.get("lines", [])

        # Pre-check balance (informational only — validator enforces this).
        total_debit = sum(Decimal(str(l.get("debit", 0))) for l in raw_lines)
        total_credit = sum(Decimal(str(l.get("credit", 0))) for l in raw_lines)
        if total_debit != total_credit:
            delta = abs(total_debit - total_credit)
            msg = (
                f"DEFECT detected in normalization: Entry '{entry_id}' is UNBALANCED "
                f"(debits={total_debit}, credits={total_credit}, delta={delta}). "
                f"Preserved as-is for validator."
            )
            warnings.append(msg)
            defects_found.append(msg)

        # Normalize lines.
        normalized_lines = []
        for line_num, line in enumerate(raw_lines, start=1):
            normalized_lines.append({
                "line_number":  line_num,
                "account_code": str(line.get("account", "")),
                "debit":        line.get("debit", 0.0),
                "credit":       line.get("credit", 0.0),
                "memo":         line.get("memo"),
            })

        # Preserve any extra source fields in raw_metadata.
        known_keys = {"id", "description", "date", "source", "lines"}
        extra = {k: v for k, v in entry.items() if k not in known_keys}

        normalized_entries.append({
            "entry_id":    entry_id,
            "description": entry.get("description", ""),
            "entry_date":  entry.get("date", ""),
            "lines":       normalized_lines,
            "prepared_by": entry.get("source"),
            "approved_by": None,
            "reference":   None,
            "tags":        [],
            "raw_metadata": {
                **extra,
                "_original_period":   raw.get("period", ""),
                "_functional_currency": raw.get("functional_currency", "USD"),
            },
        })

    output_payload = {
        "_normalized_by": "maa.normalizer v1.0",
        "_source": str(path_in.resolve()),
        "_period": raw.get("period", ""),
        "_functional_currency": raw.get("functional_currency", "USD"),
        "entries": normalized_entries,
    }

    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    log.info("Adjustments: %d entries normalized → %s", len(normalized_entries), path_out)
    return len(normalized_entries), defects_found


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 — TRIAL BALANCE NORMALIZER
# Translates multi-currency rows to USD and flags TB-level defects.
# ──────────────────────────────────────────────────────────────────────────────

def normalize_trial_balance(
    tb_path: str | Path,
    output_path: str | Path,
    fx_rates: Dict[str, Dict[str, Decimal]],
    coa_codes: set,
    warnings: List[str],
    label: str = "TB",
) -> Dict[str, Any]:
    """
    Normalize a trial balance CSV.

    Operations:
    1. Translate non-USD rows to USD using period_end rate.
    2. Group by account_code (summing translated amounts).
    3. Flag accounts not in COA (e.g. 9999, 6905).
    4. Detect duplicate account_code rows before aggregation.
    5. Check overall balance (total debits == total credits).

    Returns a summary dict with defects found.
    """
    path_in = Path(tb_path)
    path_out = Path(output_path)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    if not path_in.exists():
        warnings.append(f"TB file not found: {path_in}. Skipping.")
        return {}

    # Aggregate rows: account_code → {debit_usd, credit_usd, currencies_seen, account_name}
    aggregated: Dict[str, Dict] = {}
    unknown_accounts: List[str] = []
    duplicate_rows: List[str] = []
    raw_row_count = 0

    with open(path_in, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_row_count += 1
            code = row["account_code"].strip()
            name = row["account_name"].strip()
            ccy = row["currency"].strip().upper()

            try:
                debit_raw = Decimal(str(row["debit"]).strip() or "0")
                credit_raw = Decimal(str(row["credit"]).strip() or "0")
            except Exception:
                warnings.append(f"{label}: Cannot parse amounts for row {code}. Skipping.")
                continue

            # Translate to USD using period_end rate.
            ccy_rates = fx_rates.get(ccy, {})
            rate = ccy_rates.get("period_end", Decimal("1.0"))
            if ccy != "USD" and ccy not in fx_rates:
                warnings.append(
                    f"{label}: No FX rate for currency '{ccy}' (account {code}). "
                    f"Assuming 1:1 with USD."
                )

            debit_usd = (debit_raw * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            credit_usd = (credit_raw * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Track duplicates within same currency (suspicious — likely a data error).
            row_key = f"{code}:{ccy}"
            if row_key in aggregated.get(code, {}).get("_seen_keys", set()):
                msg = f"{label}: Duplicate row for account {code} currency {ccy}."
                warnings.append(msg)
                duplicate_rows.append(row_key)

            # Flag unknown accounts.
            if code not in coa_codes:
                if code not in unknown_accounts:
                    unknown_accounts.append(code)
                    warnings.append(
                        f"{label}: Account '{code}' ({name}) not in COA — DEFECT."
                    )

            # Aggregate.
            if code not in aggregated:
                aggregated[code] = {
                    "account_code": code,
                    "account_name": name,
                    "debit_usd": Decimal("0.00"),
                    "credit_usd": Decimal("0.00"),
                    "currencies": [],
                    "in_coa": code in coa_codes,
                    "_seen_keys": set(),
                }
            aggregated[code]["debit_usd"] += debit_usd
            aggregated[code]["credit_usd"] += credit_usd
            if ccy not in aggregated[code]["currencies"]:
                aggregated[code]["currencies"].append(ccy)
            aggregated[code]["_seen_keys"].add(row_key)

    # Write normalized TB.
    output_rows = []
    total_debit_usd = Decimal("0.00")
    total_credit_usd = Decimal("0.00")

    with open(path_out, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["account_code", "account_name", "debit_usd", "credit_usd", "currencies", "in_coa"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for code, data in sorted(aggregated.items()):
            row_out = {
                "account_code": code,
                "account_name": data["account_name"],
                "debit_usd":    str(data["debit_usd"]),
                "credit_usd":   str(data["credit_usd"]),
                "currencies":   "|".join(data["currencies"]),
                "in_coa":       str(data["in_coa"]).lower(),
            }
            writer.writerow(row_out)
            total_debit_usd += data["debit_usd"]
            total_credit_usd += data["credit_usd"]
            output_rows.append(row_out)

    # Check overall balance.
    tb_delta = total_debit_usd - total_credit_usd
    tb_balanced = tb_delta == Decimal("0.00")
    if not tb_balanced:
        msg = (
            f"{label}: UNBALANCED — Total Debits (USD) = {total_debit_usd:,.2f}, "
            f"Total Credits (USD) = {total_credit_usd:,.2f}, "
            f"Delta = {abs(tb_delta):,.2f}. This is a known seeded defect."
        )
        warnings.append(msg)

    summary = {
        "label": label,
        "raw_rows": raw_row_count,
        "accounts": len(aggregated),
        "total_debit_usd": float(total_debit_usd),
        "total_credit_usd": float(total_credit_usd),
        "delta_usd": float(tb_delta),
        "balanced": tb_balanced,
        "unknown_accounts": unknown_accounts,
        "duplicate_rows": duplicate_rows,
    }

    log.info(
        "%s: %d accounts (USD), debits=%.2f, credits=%.2f, balanced=%s → %s",
        label, len(aggregated), float(total_debit_usd), float(total_credit_usd),
        tb_balanced, path_out,
    )
    return summary


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 5 — MASTER RUNNER
# ──────────────────────────────────────────────────────────────────────────────

def normalize_all(
    inputs_dir: str | Path = "inputs",
    data_dir: str | Path = "data",
) -> Dict[str, Any]:
    """
    Run the full normalization pipeline:
      1. Load FX rates
      2. Normalize COA → build code lookup set
      3. Normalize adjustments
      4. Normalize current TB (with FX translation + defect detection)
      5. Normalize prior period TB

    Returns a summary dict with all defects found across all files.
    """
    inputs_dir = Path(inputs_dir)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    warnings: List[str] = []
    all_defects: List[str] = []

    print("\n" + "=" * 60)
    print("  MAA Input Normalizer")
    print(f"  Source: {inputs_dir.resolve()}")
    print(f"  Output: {data_dir.resolve()}")
    print("=" * 60)

    # ── 1. FX Rates ───────────────────────────────────────────────────────────
    fx_rates = load_fx_rates(inputs_dir / "fx_rates.csv")
    print(f"\n  FX Rates loaded: {sorted(fx_rates.keys())}")

    # ── 2. COA ────────────────────────────────────────────────────────────────
    coa_count = normalize_coa(
        inputs_dir / "chart_of_accounts.csv",
        data_dir / "chart_of_accounts.csv",
        warnings,
    )
    # Build the valid code set for TB validation.
    coa_codes: set = set()
    with open(data_dir / "chart_of_accounts.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            coa_codes.add(row["account_code"])
    print(f"  COA: {coa_count} postable accounts (headers stripped)")

    # ── 3. Adjustments ────────────────────────────────────────────────────────
    entry_count, adj_defects = normalize_adjustments(
        inputs_dir / "manual_adjustments.json",
        data_dir / "manual_adjustments.json",
        warnings,
    )
    all_defects.extend(adj_defects)
    print(f"  Adjustments: {entry_count} entries")
    if adj_defects:
        for d in adj_defects:
            print(f"    DEFECT: {d}")

    # ── 4. Current Period TB ──────────────────────────────────────────────────
    tb_summary = normalize_trial_balance(
        inputs_dir / "trial_balance.csv",
        data_dir / "trial_balance_normalized.csv",
        fx_rates,
        coa_codes,
        warnings,
        label="Current TB",
    )
    all_defects.extend([
        f"TB DEFECT: unknown account {c}" for c in tb_summary.get("unknown_accounts", [])
    ])
    if not tb_summary.get("balanced", True):
        all_defects.append(
            f"TB DEFECT: Unbalanced by USD {tb_summary['delta_usd']:,.2f}"
        )
    print(f"\n  Current TB: {tb_summary.get('accounts', 0)} accounts after FX translation")
    print(f"    Total Debits:  USD {tb_summary.get('total_debit_usd', 0):>15,.2f}")
    print(f"    Total Credits: USD {tb_summary.get('total_credit_usd', 0):>15,.2f}")
    print(f"    Delta:         USD {tb_summary.get('delta_usd', 0):>15,.2f}  {'BALANCED' if tb_summary.get('balanced') else '*** UNBALANCED ***'}")
    if tb_summary.get("unknown_accounts"):
        print(f"    Unknown accounts: {tb_summary['unknown_accounts']}")
    if tb_summary.get("duplicate_rows"):
        print(f"    Duplicate rows: {tb_summary['duplicate_rows']}")

    # ── 5. Prior Period TB ────────────────────────────────────────────────────
    pp_summary = normalize_trial_balance(
        inputs_dir / "prior_period_tb.csv",
        data_dir / "prior_period_tb_normalized.csv",
        fx_rates,
        coa_codes,
        warnings,
        label="Prior Period TB",
    )
    all_defects.extend([
        f"Prior TB DEFECT: unknown account {c}" for c in pp_summary.get("unknown_accounts", [])
    ])
    print(f"\n  Prior Period TB: {pp_summary.get('accounts', 0)} accounts")
    if pp_summary.get("unknown_accounts"):
        print(f"    Unknown accounts: {pp_summary['unknown_accounts']}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n  Total defects detected across all input files: {len(all_defects)}")
    for i, d in enumerate(all_defects, 1):
        print(f"    {i}. {d}")

    print("\n  Warnings:")
    for w in warnings:
        print(f"    ⚠  {w}")

    print("\n" + "=" * 60)

    return {
        "coa_accounts": coa_count,
        "coa_codes": coa_codes,
        "entry_count": entry_count,
        "tb_summary": tb_summary,
        "prior_period_summary": pp_summary,
        "defects_found": all_defects,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    result = normalize_all()
    sys.exit(0)
