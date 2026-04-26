# IntegrateAI — Reflection Document
**Manual Adjustments Agent (MAA) Prototype**

### 1. What I would build differently with 3 months
With three months, I would evolve the system from a pipeline script into a distributed, event-driven architecture designed for enterprise integration:
*   **Vectorized RAG for COA Mapping:** Instead of providing the LLM with a simple list of valid account codes, I would build a semantic search tool over the company’s accounting policies and historical journals. This would allow the LLM to map ambiguous entries based on past precedent rather than just text similarity.
*   **Human-in-the-Loop (HITL) Workflow:** I would build a stateful review queue. When the LLM suggests a correction for a quarantined entry, it would route to a UI where a finance team member can approve or modify it, maintaining the original `trace_id` lineage.
*   **Streaming Data Ingestion:** Move away from batch CSV/JSON loads and integrate directly via ERP APIs (e.g., NetSuite SuiteTalk) or an event bus (Kafka), allowing continuous, real-time validation of entries as they are drafted.
*   **Advanced FX Translation:** Implement historical rate lookups and average rate translation logic for P&L accounts, rather than a simplified period-end rate application.

### 2. Where the prototype would break at scale
*   **Multi-Entity Consolidation:** The current pipeline assumes a single functional currency (USD) and a flat trial balance. It has no concept of entity hierarchies, intercompany elimination rules across subsidiaries, or varying local GAAP vs. group GAAP mappings.
*   **LLM Context Limits:** The `ValidatorHandoff` model passes the entire entry. For a massive period-end adjustment with thousands of lines, this will blow out the context window and trigger a rate limit or timeout. We would need to chunk entries or pass only the failing lines to the LLM.
*   **COA Lookup Performance:** The current Python set lookup `if account not in coa_codes:` works for 80 accounts. At 100,000 accounts across multiple ledgers, we need a dedicated reference data service (e.g., Redis) to handle fast, memory-efficient lookups and point-in-time validity checks.

### 3. How AI tools were used (helped vs. misled)
*   **Helped:** AI tooling was invaluable for rapidly generating the boilerplate for the Next.js/Tailwind frontend, Pydantic models, and the mock dataset. It accelerated the transformation of raw logic into a polished dashboard. It was also excellent at writing regex patterns for parsing and mapping schemas.
*   **Misled:** The AI initially struggled with the strict deterministic boundary constraint. It repeatedly tried to have the LLM agent perform mathematical checks (e.g., `debits == credits`) and correct the data silently. It required strong prompt engineering and system instructions to enforce that the LLM should *only* narrate, while Python handles the arithmetic. Additionally, when writing the normalizer, the AI initially hallucinated missing fields instead of preserving the exact shape of the messy data.

### 4. One thing about this problem you are underestimating
**The semantics of "Suspense" and "Historical" accounts.**
A purely technical approach assumes that any account not in the current COA is a defect. However, in real-world M&A integrations or ERP migrations, prior-period trial balances often contain accounts that have been deliberately sunset, rolled up, or parked in suspense. An agent flagging these simply as "Account not found" is insufficient. The system needs temporal awareness (point-in-time COA validity) and the ability to distinguish between a genuinely invalid code and a legitimate legacy mapping that requires a defined roll-forward rule.
