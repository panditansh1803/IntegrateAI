/**
 * types.ts — TypeScript interfaces mirroring the backend Pydantic models.
 * Used by the dashboard to type-check the validation_results.json payload.
 */

export type EntryStatus = "ACCEPTED" | "REJECTED" | "QUARANTINED";

export type ErrorCode =
  | "ERR_UNBALANCED"
  | "ERR_INVALID_ACCOUNT"
  | "ERR_IC_CIRCULAR"
  | "ERR_SCHEMA_INVALID"
  | "ERR_DUPLICATE_ID"
  | "ERR_BOTH_SIDES"
  | "ERR_ZERO_LINE";

export type LLMStatus =
  | "OK"
  | "TIMEOUT"
  | "PARSE_FAILURE"
  | "RATE_LIMITED"
  | "NOT_CALLED"
  | "SKIPPED";

export type RiskSeverity = "HIGH" | "MEDIUM" | "LOW";

export type CheckName =
  | "SCHEMA"
  | "BALANCE"
  | "COA_LOOKUP"
  | "IC_CIRCULAR"
  | "DUPLICATE_ID";

export interface JournalLine {
  line_number: number;
  account_code: string;
  debit: string; // Decimal serialised as string
  credit: string;
  memo: string | null;
}

export interface JournalEntry {
  entry_id: string;
  description: string;
  entry_date: string;
  lines: JournalLine[];
  prepared_by: string | null;
  approved_by: string | null;
  reference: string | null;
  tags: string[];
  raw_metadata: Record<string, unknown>;
}

export interface CheckResult {
  check: CheckName;
  passed: boolean;
  error_code: ErrorCode | null;
  detail: Record<string, unknown>;
}

export interface PlainEnglishExplanation {
  summary: string;
  detail: string;
  severity: RiskSeverity;
  suggested_correction: string | null;
  risk_flags: string[];
  llm_suggestion_verified: boolean;
}

export interface ValidationResult {
  trace_id: string;
  entry_id: string;
  status: EntryStatus;
  error_codes: ErrorCode[];
  checks_run: CheckResult[];
  llm_explanation: PlainEnglishExplanation | null;
  llm_status: LLMStatus;
  llm_model: string | null;
  llm_latency_ms: number | null;
  raw_entry_snapshot: JournalEntry;
  timestamp_utc: string;
  pipeline_version: string;
}

export interface PipelineRunSummary {
  run_id: string;
  pipeline_version: string;
  started_at: string;
  completed_at: string | null;
  total_entries: number;
  accepted: number;
  rejected: number;
  quarantined: number;
  coa_loaded: boolean;
  coa_account_count: number;
  ic_check_active: boolean;
  ic_check_skipped_reason: string | null;
  warnings: string[];
  results: ValidationResult[];
}
