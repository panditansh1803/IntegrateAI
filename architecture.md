# IntegrateAI — Architecture Document
**Manual Adjustments Agent (MAA)**

## 1. Agent Topology
**Chosen Topology:** Orchestrator with Specialized Components (State Machine Pipeline)

Rather than a single autonomous agent (e.g., an LLM with tools in a `while True` loop), this system is built as a highly structured pipeline orchestrated by a central state machine.
*   **Normalizer:** A deterministic Python adapter that ingests messy ERP data (different schemas, missing FX rates, inferred intercompany flags) and normalizes it into pipeline-compatible Pydantic models.
*   **Validator:** A pure Python deterministic engine that runs strict business rules (Balance, Schema, COA Lookup, IC Circularity).
*   **LLM Explainer (The Agent):** A specialized sub-agent invoked *only* when the Validator flags a failure. It is handed a tightly scoped context (`ValidatorHandoff`) containing the raw entry, exact error codes, and a subset of valid COA accounts.
*   **Audit Logger:** An append-only JSONL sink.

**Justification:** A swarm of LLM agents is unnecessary and dangerous for this specific slice. Finance requires idempotency and traceability. If an entry fails because debits exceed credits by $3,500, we don't need an LLM to discover that fact via a tool; we need deterministic code to assert it. The LLM's true value is translating that failure into plain-English narratives for the finance team and suggesting semantically appropriate reclassifications.

## 2. Deterministic Code vs. LLM Reasoning Boundary
Generating financial statements and enforcing double-entry arithmetic is fundamentally a deterministic process. The boundary is strictly enforced:
*   **Arithmetic & Rules = Deterministic Code:** The `Validator` uses Python's `Decimal` class for precision. It verifies `debits == credits`, checks if account codes exist in the defined `COA` set, and detects duplicate IDs. LLMs are notoriously unreliable at arithmetic and are expressly forbidden from performing it here.
*   **Narration & Semantic Matching = LLM Reasoning:** The LLM actually "earns its keep" by interpreting the *context* of a failure. For example, if an entry hits an invalid account, the LLM analyzes the `description` and `memo` (e.g., "Conference travel to NYC") and suggests the closest valid COA account (e.g., `6310 Travel and Entertainment`). It generates a human-readable `summary` and a detailed `detail` explanation, attaching a semantic `RiskSeverity` (e.g., flagging an entry as HIGH risk if it appears to be a prior-period reversal).

## 3. Handling Critical Failure Modes
The prototype detects and safely handles the following seeded production-killers:
*   **Hallucinated Account Mappings:** The LLM is forced to output structured JSON via Pydantic (`PlainEnglishExplanation`). If it suggests a corrective account code, the `Orchestrator` explicitly re-validates that suggestion against the active COA set before tagging `llm_suggestion_verified = True`.
*   **Debits ≠ Credits After Adjustments:** The `Normalizer` detects and warns on unbalanced trial balances (e.g., delta $73,613). The `Validator` enforces strict balancing on manual adjustments (e.g., JE-002 rejected with `ERR_UNBALANCED` for a $3,500 delta).
*   **An Account Fits No COA Node:** The `Normalizer` flags unknown accounts in the TB (e.g., `9999` and `6905`). The `Validator` rejects adjustments hitting invalid accounts (`ERR_INVALID_ACCOUNT`). Additionally, it detects "orphaned" headers in the COA hierarchy (nodes with no mapped children).
*   **FX Gaps:** The `Normalizer` explicitly checks for missing `period_end` rates (e.g., the seeded GBP defect) and logs an `ERR_FX_MISSING_RATE` warning, preventing silent translation failures or downstream balance sheet imbalances.
*   **Circular Intercompany Entries:** The `Validator` checks if an entry debits and credits accounts both flagged as `is_intercompany` (e.g., JE-008 hitting account `2170` on both sides) and rejects it with `ERR_IC_CIRCULAR`.

## 4. Validation and Self-Correction Loop
Before the system returns a final `ValidationResult` payload, it follows this loop:
1.  **Pre-flight validation (Pydantic):** The raw JSON is validated against strict Pydantic models (e.g., `JournalEntry`). If a line lacks both a debit and credit, it fails schema validation instantly.
2.  **Business Logic Check (Validator):** The pipeline runs all 5 deterministic checks.
3.  **LLM Explainer Call:** If *any* check fails, the LLM is called with the exact error context.
4.  **Self-Correction / Verification:** The LLM output is parsed. If the parsing fails (invalid JSON or schema mismatch), the `llm_explainer` invokes a retry loop with exponential backoff (max 2 retries).
5.  **Quarantine Fallback:** If the LLM completely fails or API times out, the entry is *not* dropped. It is routed to `EntryStatus.QUARANTINED` with `llm_status=TIMEOUT/PARSE_FAILURE`. The pipeline continues processing the remaining entries.

## 5. Auditor Traceability
Finance software without traceability is unusable. The system guarantees that any cell on a final statement can be traced back to its origin:
*   **Trace Lineage ID:** Every pipeline run generates a unique `run_id`. Every single journal entry processed within that run gets a globally unique `trace_id` (e.g., `MAA-20260426-0001`).
*   **Immutable Snapshots:** The `AuditLogRecord` written to the `validation_log.jsonl` contains the full `raw_entry_snapshot` exactly as it was received. It is never mutated.
*   **Append-Only System:** The audit log is append-only. If a user corrects JE-002 and resubmits it, it receives a *new* `trace_id` and a new append row, preserving the history of the original failure.
*   **Deterministic Re-run:** Because the business logic is decoupled from the stochastic LLM, an auditor can re-run the `Validator` on the `raw_entry_snapshot` and reliably reproduce the exact `error_codes` array at any point in the future.
