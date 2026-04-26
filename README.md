# IntegrateAI — Manual Adjustments Agent (MAA)

> **ERP-Driven Financial Statement Generator — Prototype Slice**
> A targeted, audit-first pipeline that validates manual journal entries against a Chart of Accounts, detects violations deterministically, and explains rejections in plain English via an LLM.

---

## Architecture

```
inputs/                          Company-provided raw data (dirty by design)
  ├── chart_of_accounts.csv
  ├── manual_adjustments.json
  ├── trial_balance.csv
  ├── prior_period_tb.csv
  └── fx_rates.csv
          │
          ▼
backend/normalizer.py            Schema adapter (field remap, FX translation, defect detection)
          │
          ▼  data/               Normalised, pipeline-ready files
          │
          ▼
backend/orchestrator.py          State machine: Parse → Validate → Explain → Log
    ├── backend/validator.py     Deterministic checks (pure Python, zero LLM)
    │       ├── CHECK 1: DUPLICATE_ID
    │       ├── CHECK 2: SCHEMA
    │       ├── CHECK 3: BALANCE      ← Decimal arithmetic, not float
    │       ├── CHECK 4: COA_LOOKUP
    │       └── CHECK 5: IC_CIRCULAR
    └── backend/llm_explainer.py LLM narration (called ONLY on failure)
          │
          ▼
output/
  ├── validation_results.json    → consumed by Next.js frontend
  └── validation_log.jsonl       → append-only audit trail

frontend/                        Next.js 16 + Tailwind dashboard
```

### Core Philosophy

| Principle | How |
|---|---|
| **LLMs earn their keep** | LLMs called ONLY for plain-English explanations. All arithmetic is pure Python Decimal. |
| **Auditability is King** | Every entry gets a `trace_id`. `validation_log.jsonl` is append-only and never mutated. |
| **Handle the Mess** | Input data is processed as-is. Defects are detected, not silently cleaned. |
| **Fail Loud, Fail Safe** | LLM failure → QUARANTINED (not silently dropped). Pipeline continues. |

---

## Detected Defects in Company Data

The `inputs/` folder contains intentionally seeded defects. The pipeline finds them all:

| Entry / File | Defect | Error Code |
|---|---|---|
| JE-002 | Debits $28,500 ≠ Credits $25,000 (delta $3,500) | `ERR_UNBALANCED` |
| JE-005 | Account `6315` does not exist in COA | `ERR_INVALID_ACCOUNT` |
| JE-008 | Account `2170` (IC Payable) on both debit & credit side | `ERR_IC_CIRCULAR` |
| trial_balance.csv | Account `9999` (Suspense-Unmapped) not in COA | TB Defect |
| trial_balance.csv | Account `6310` appears twice (duplicate row) | TB Defect |
| trial_balance.csv | Total debits ≠ credits by **USD 73,613** | TB Imbalance |
| prior_period_tb.csv | Account `6905` not in COA | TB Defect |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+

### Backend

```bash
# Install Python dependencies
pip install pydantic

# Step 1: Normalize all company input files
python -m backend.normalizer

# Step 2: Run the full validation pipeline (mock LLM, no API key needed)
python -m backend.main

# With a real LLM (Gemini):
set GOOGLE_API_KEY=your-key-here
python -m backend.main --provider gemini

# With OpenAI:
set OPENAI_API_KEY=your-key-here
python -m backend.main --provider openai
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:3000
```

---

## Project Structure

```
IntegrateAI/
├── architecture_draft.md        Step 1: Architecture document
├── inputs/                      Company-provided raw input files (DO NOT MODIFY)
├── data/                        Normalised files (generated — do not edit manually)
├── output/                      Pipeline output (generated)
├── backend/
│   ├── models.py                Pydantic v2 data models (all enums, entities)
│   ├── normalizer.py            Input adapter: inputs/ → data/
│   ├── validator.py             5-check deterministic validation engine
│   ├── llm_explainer.py         LLM explainer (Gemini/OpenAI/Mock)
│   ├── orchestrator.py          Pipeline state machine
│   ├── audit_logger.py          Append-only JSONL writer
│   └── main.py                  CLI + FastAPI entry point
├── frontend/                    Next.js 16 + Tailwind dashboard
│   └── src/
│       ├── app/
│       │   ├── globals.css      Design system (glassmorphism, tokens)
│       │   ├── layout.tsx
│       │   ├── page.tsx         Dashboard page
│       │   └── types.ts         TypeScript interfaces
│       └── components/
│           ├── StatsBar.tsx     Summary metric cards
│           └── EntryRow.tsx     Expandable entry row with LLM explanation
└── test_validator.py            Integration test suite (12 mock scenarios)
```

---

## LLM Provider Configuration

| Provider | Env Variable | Model Default |
|---|---|---|
| Gemini (default) | `GOOGLE_API_KEY` | `gemini-2.0-flash` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` |
| Mock (no key) | — | `mock-explainer-v1` |

Override model: `GEMINI_MODEL=gemini-2.5-pro` or `OPENAI_MODEL=gpt-4o`

---

## Audit Trail

Every entry produces exactly one JSONL record:

```json
{
  "trace_id": "MAA-20260426-0002",
  "entry_id": "JE-002",
  "status": "REJECTED",
  "error_codes": ["ERR_UNBALANCED"],
  "checks_run": [...],
  "llm_explanation": {
    "summary": "Entry JE-002 rejected: debits exceed credits by $3,500.00.",
    "severity": "HIGH",
    ...
  },
  "raw_entry_snapshot": { ... }
}
```

Resubmissions append new records — existing records are never overwritten.

---

*Period: 2024-Q4 · Functional currency: USD*
