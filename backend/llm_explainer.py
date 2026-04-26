"""
llm_explainer.py — LLM-Powered Explanation Engine
===================================================
Called ONLY when the deterministic validator flags errors.
The LLM reads the structured ValidatorHandoff and produces a
PlainEnglishExplanation for the finance team.

Design rules:
  - The LLM never performs arithmetic. It only narrates.
  - All numbers in the prompt come from the validator's deterministic output.
  - The LLM's structured output is parsed via Pydantic. If parsing fails,
    the entry is QUARANTINED with llm_status=PARSE_FAILURE.
  - Retry logic: 2 retries with exponential backoff (1s, 2s).
  - A "mock" mode is available for testing without API keys.

Supported providers (via LLM_PROVIDER env var):
  - "gemini"  → Google Generative AI (google-generativeai SDK)
  - "openai"  → OpenAI ChatCompletions API
  - "mock"    → Deterministic mock responses for testing (default)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from backend.models import (
    ErrorCode,
    LLMStatus,
    PlainEnglishExplanation,
    RiskSeverity,
    ValidatorHandoff,
)

log = logging.getLogger("maa.llm_explainer")

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 — SYSTEM PROMPT
# This prompt is the core contract with the LLM. Every design choice here
# exists for a reason: preventing hallucination, enforcing structure, and
# keeping the LLM focused on narration (not computation).
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Explanation Agent for a financial journal entry validation system.
Your ONLY job is to translate machine error codes into clear, plain-English explanations
that a non-technical finance team member can understand and act upon.

STRICT RULES:
1. You do NOT perform any arithmetic. All numbers are pre-computed and provided to you.
   Simply reference them in your explanation.
2. You MUST respond with ONLY valid JSON matching the exact schema below. No markdown,
   no code fences, no commentary outside the JSON object.
3. If you suggest an account code correction, you MUST choose from the valid_accounts
   list provided. Do NOT invent account codes.
4. Use professional but accessible language. Avoid jargon unless explaining it.
5. Be specific: name the entry ID, line numbers, account codes, and dollar amounts.
6. For severity: HIGH = data integrity/fraud risk, MEDIUM = policy violation,
   LOW = advisory/suggestion.

RESPONSE SCHEMA (JSON):
{
  "summary": "One sentence for dashboard display (max 300 chars)",
  "detail": "Full explanation with specific line numbers, accounts, amounts (max 2000 chars)",
  "severity": "HIGH" | "MEDIUM" | "LOW",
  "suggested_correction": "Optional corrective action or null",
  "risk_flags": ["list", "of", "semantic", "risk", "signals"]
}

ERROR CODE REFERENCE:
- ERR_UNBALANCED: Total debits do not equal total credits. Reference the exact delta.
- ERR_INVALID_ACCOUNT: One or more account codes do not exist in the Chart of Accounts.
- ERR_IC_CIRCULAR: An intercompany account appears on both debit and credit sides of the
  same entry, creating a circular posting with no economic substance.
- ERR_SCHEMA_INVALID: The entry has structural problems (missing fields, duplicate lines).
- ERR_DUPLICATE_ID: This entry ID was already processed — possible accidental resubmission.
- ERR_BOTH_SIDES: A single line has both a non-zero debit and credit amount.
- ERR_ZERO_LINE: A single line has zero for both debit and credit.
"""


def _build_user_prompt(handoff: ValidatorHandoff) -> str:
    """
    Construct the user prompt from the ValidatorHandoff.
    Contains all context the LLM needs — no external lookups required.
    """
    # Serialize the entry for the LLM, but exclude internal Pydantic metadata.
    entry_data = handoff.raw_entry.model_dump(mode="json")

    prompt_parts = [
        f"ENTRY ID: {handoff.entry_id}",
        f"TRACE ID: {handoff.trace_id}",
        f"DESCRIPTION: {handoff.raw_entry.description}",
        f"DATE: {handoff.raw_entry.entry_date}",
        f"",
        f"ERROR CODES TRIGGERED: {[code.value for code in handoff.error_codes]}",
        f"",
        f"ERROR DETAILS (pre-computed by deterministic validator — do NOT recalculate):",
        json.dumps(handoff.error_details, indent=2, default=str),
        f"",
        f"FULL ENTRY DATA:",
        json.dumps(entry_data, indent=2, default=str),
        f"",
        f"VALID ACCOUNT CODES (use ONLY these for any suggestions):",
        json.dumps(handoff.coa_sample, indent=2),
        f"",
        f"Now produce the JSON explanation following the schema in your instructions.",
    ]
    return "\n".join(prompt_parts)


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 — PROVIDER ABSTRACTION
# Each provider implements _call_llm_raw() → (raw_text, model_name, latency_ms)
# ──────────────────────────────────────────────────────────────────────────────

class LLMCallError(RuntimeError):
    """Raised when the LLM API call fails after all retries."""
    def __init__(self, message: str, status: LLMStatus):
        super().__init__(message)
        self.llm_status = status


def _call_gemini(system: str, user: str, timeout: int) -> Tuple[str, str]:
    """Call Google Gemini via google-generativeai SDK."""
    import google.generativeai as genai

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMCallError(
            "GOOGLE_API_KEY or GEMINI_API_KEY env var not set.",
            LLMStatus.TIMEOUT,
        )

    genai.configure(api_key=api_key)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system,
        generation_config=genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,   # Low temperature for deterministic-ish output.
            max_output_tokens=1500,
        ),
    )
    response = model.generate_content(user, request_options={"timeout": timeout})
    return response.text, model_name


def _call_openai(system: str, user: str, timeout: int) -> Tuple[str, str]:
    """Call OpenAI ChatCompletions API."""
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMCallError(
            "OPENAI_API_KEY env var not set.",
            LLMStatus.TIMEOUT,
        )

    client = OpenAI(api_key=api_key, timeout=timeout)
    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=1500,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content, model_name


def _call_mock(system: str, user: str, timeout: int) -> Tuple[str, str]:
    """
    Deterministic mock LLM for testing without API keys.
    Generates a reasonable explanation from the error codes alone.
    No network calls, no randomness, no cost.
    """
    # Parse the error codes from the user prompt.
    import re
    codes_match = re.search(r"ERROR CODES TRIGGERED: \[(.+?)\]", user)
    codes_str = codes_match.group(1) if codes_match else ""
    codes = [c.strip().strip("'\"") for c in codes_str.split(",")]

    # Parse entry ID.
    id_match = re.search(r"ENTRY ID: (\S+)", user)
    entry_id = id_match.group(1) if id_match else "UNKNOWN"

    # Build explanation parts.
    detail_parts: List[str] = []
    severity = "MEDIUM"
    risk_flags: List[str] = []
    suggestion = None

    for code in codes:
        if code == "ERR_UNBALANCED":
            detail_parts.append(
                f"The total debits and total credits for this entry do not match. "
                f"Please review the line amounts and ensure they balance before resubmission."
            )
            severity = "HIGH"
        elif code == "ERR_INVALID_ACCOUNT":
            detail_parts.append(
                f"One or more account codes used in this entry do not exist in the "
                f"approved Chart of Accounts. Please verify each account code."
            )
        elif code == "ERR_IC_CIRCULAR":
            detail_parts.append(
                f"An intercompany account appears on both the debit and credit side "
                f"of this entry. This creates a circular posting with no economic "
                f"substance and is typically prohibited."
            )
            severity = "HIGH"
            risk_flags.append("circular-intercompany")
        elif code == "ERR_DUPLICATE_ID":
            detail_parts.append(
                f"This entry ID has already been processed. If this is a correction, "
                f"please assign a new entry ID."
            )
            risk_flags.append("duplicate-submission")
        elif code == "ERR_SCHEMA_INVALID":
            detail_parts.append(
                f"The entry has structural issues (e.g., missing required fields, "
                f"duplicate line numbers, or insufficient line count)."
            )

    # Check for risk signals in the user prompt.
    if "reversal" in user.lower() or "prior period" in user.lower():
        risk_flags.append("prior-period-reversal")
    if "year-end" in user.lower() or "rushed" in user.lower():
        risk_flags.append("rushed-year-end-entry")

    summary = f"Entry {entry_id} was rejected due to: {', '.join(codes)}."
    detail = f"Entry {entry_id}: " + " ".join(detail_parts)

    mock_response = {
        "summary": summary[:300],
        "detail": detail[:2000],
        "severity": severity,
        "suggested_correction": suggestion,
        "risk_flags": risk_flags,
    }

    return json.dumps(mock_response), "mock-explainer-v1"


# Provider dispatch table.
_PROVIDERS = {
    "gemini": _call_gemini,
    "openai": _call_openai,
    "mock":   _call_mock,
}


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 — RETRY LOGIC & RESPONSE PARSING
# ──────────────────────────────────────────────────────────────────────────────

def _extract_json_from_response(raw: str) -> str:
    """
    Defensive extraction: strip markdown fences if the LLM wraps its JSON
    in ```json ... ``` despite being told not to.
    """
    text = raw.strip()
    # Strip markdown code fences.
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


def _parse_llm_response(raw_text: str) -> PlainEnglishExplanation:
    """
    Parse the raw LLM response text into a Pydantic PlainEnglishExplanation.
    Raises ValidationError if the response doesn't match the schema.
    """
    cleaned = _extract_json_from_response(raw_text)
    data = json.loads(cleaned)
    return PlainEnglishExplanation(**data)


def explain_errors(
    handoff: ValidatorHandoff,
    coa_valid_codes: Optional[set] = None,
    provider: Optional[str] = None,
    timeout: int = 10,
    max_retries: int = 2,
) -> Tuple[Optional[PlainEnglishExplanation], LLMStatus, Optional[str], Optional[int]]:
    """
    Call the LLM to explain validation errors for a single entry.

    Args:
        handoff:          Typed contract from the deterministic validator.
        coa_valid_codes:  Set of valid account codes for re-validating LLM suggestions.
        provider:         LLM provider key ("gemini", "openai", "mock").
                          Falls back to LLM_PROVIDER env var, then "mock".
        timeout:          Per-call timeout in seconds.
        max_retries:      Number of retry attempts on transient failure.

    Returns:
        (explanation, llm_status, model_name, latency_ms)
        - explanation is None on failure (QUARANTINED entries).
        - llm_status indicates the outcome of the LLM call.
        - model_name is the model identifier used.
        - latency_ms is the round-trip time.
    """
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    provider = provider.lower().strip()

    if provider not in _PROVIDERS:
        log.error("Unknown LLM_PROVIDER '%s'. Falling back to 'mock'.", provider)
        provider = "mock"

    call_fn = _PROVIDERS[provider]
    system = SYSTEM_PROMPT
    user = _build_user_prompt(handoff)

    log.info(
        "[%s] Calling LLM (%s) for entry '%s' — errors: %s",
        handoff.trace_id,
        provider,
        handoff.entry_id,
        [c.value for c in handoff.error_codes],
    )

    last_error: Optional[str] = None
    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries retries
        start_ms = int(time.time() * 1000)
        try:
            raw_text, model_name = call_fn(system, user, timeout)
            latency_ms = int(time.time() * 1000) - start_ms

            log.debug(
                "[%s] LLM response received (%dms, %d chars)",
                handoff.trace_id, latency_ms, len(raw_text),
            )

            # Parse the response into Pydantic model.
            explanation = _parse_llm_response(raw_text)

            # Post-processing: re-validate any suggested account code.
            if explanation.suggested_correction and coa_valid_codes:
                # Extract account-code-like tokens from the suggestion.
                import re
                suggested_codes = re.findall(r"\b[A-Za-z0-9\-]{2,20}\b", explanation.suggested_correction)
                verified = any(code in coa_valid_codes for code in suggested_codes)
                explanation = explanation.model_copy(update={"llm_suggestion_verified": verified})
                if not verified:
                    log.warning(
                        "[%s] LLM suggested correction contains no verifiable account codes.",
                        handoff.trace_id,
                    )

            log.info(
                "[%s] LLM explanation generated: severity=%s, flags=%s",
                handoff.trace_id,
                explanation.severity.value,
                explanation.risk_flags,
            )
            return explanation, LLMStatus.OK, model_name, latency_ms

        except json.JSONDecodeError as exc:
            latency_ms = int(time.time() * 1000) - start_ms
            last_error = f"JSON parse error: {exc}"
            log.warning(
                "[%s] LLM response not valid JSON (attempt %d/%d): %s",
                handoff.trace_id, attempt, max_retries + 1, exc,
            )
            if attempt <= max_retries:
                time.sleep(attempt)  # Exponential-ish backoff: 1s, 2s
                continue
            return None, LLMStatus.PARSE_FAILURE, None, latency_ms

        except ValidationError as exc:
            latency_ms = int(time.time() * 1000) - start_ms
            last_error = f"Pydantic validation error: {exc.error_count()} errors"
            log.warning(
                "[%s] LLM response failed schema validation (attempt %d/%d): %s",
                handoff.trace_id, attempt, max_retries + 1, exc,
            )
            if attempt <= max_retries:
                time.sleep(attempt)
                continue
            return None, LLMStatus.PARSE_FAILURE, None, latency_ms

        except LLMCallError as exc:
            latency_ms = int(time.time() * 1000) - start_ms
            last_error = str(exc)
            log.error(
                "[%s] LLM call error (attempt %d/%d): %s",
                handoff.trace_id, attempt, max_retries + 1, exc,
            )
            return None, exc.llm_status, None, latency_ms

        except Exception as exc:
            latency_ms = int(time.time() * 1000) - start_ms
            last_error = f"Unexpected error: {type(exc).__name__}: {exc}"
            log.error(
                "[%s] Unexpected LLM error (attempt %d/%d): %s",
                handoff.trace_id, attempt, max_retries + 1, exc,
            )
            if attempt <= max_retries:
                time.sleep(attempt)
                continue
            return None, LLMStatus.TIMEOUT, None, latency_ms

    # Should not reach here, but safety net.
    log.error("[%s] LLM exhausted all retries. Last error: %s", handoff.trace_id, last_error)
    return None, LLMStatus.TIMEOUT, None, None
