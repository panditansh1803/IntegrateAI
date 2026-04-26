"use client";

import React, { useState } from "react";
import type { ValidationResult, EntryStatus, CheckResult } from "@/app/types";

/**
 * EntryRow — One expandable row in the validation results table.
 * Click to expand and see full check results + LLM explanation.
 */
export default function EntryRow({
  result,
  index,
}: {
  result: ValidationResult;
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const entry = result.raw_entry_snapshot;
  const status = result.status;
  const explanation = result.llm_explanation;

  /** Status → CSS class mappings */
  const statusBadge: Record<EntryStatus, string> = {
    ACCEPTED: "badge-accepted",
    REJECTED: "badge-rejected",
    QUARANTINED: "badge-quarantined",
  };
  const statusDot: Record<EntryStatus, string> = {
    ACCEPTED: "pulse-dot-accepted",
    REJECTED: "pulse-dot-rejected",
    QUARANTINED: "pulse-dot-quarantined",
  };
  const rowHover: Record<EntryStatus, string> = {
    ACCEPTED: "entry-row-accepted",
    REJECTED: "entry-row-rejected",
    QUARANTINED: "entry-row-quarantined",
  };

  /** Severity badge colors */
  const severityColor: Record<string, string> = {
    HIGH: "text-[var(--status-rejected)] bg-[var(--status-rejected-bg)] border-[var(--status-rejected-border)]",
    MEDIUM: "text-[var(--status-quarantined)] bg-[var(--status-quarantined-bg)] border-[var(--status-quarantined-border)]",
    LOW: "text-[var(--accent-blue)] bg-[rgba(59,130,246,0.08)] border-[rgba(59,130,246,0.25)]",
  };

  /** Format monetary values */
  const fmt = (val: string | number) => {
    const n = typeof val === "string" ? parseFloat(val) : val;
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
    }).format(n);
  };

  return (
    <div
      className={`animate-fade-in-up`}
      style={{ animationDelay: `${index * 40}ms` }}
    >
      {/* ── Clickable Row ─────────────────────────────────────────────── */}
      <div
        id={`entry-row-${result.entry_id}-${result.trace_id}`}
        className={`entry-row ${rowHover[status]} px-4 md:px-6 py-4 flex items-center gap-3 md:gap-5 select-none`}
        onClick={() => setOpen(!open)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && setOpen(!open)}
        aria-expanded={open}
      >
        {/* Pulse dot */}
        <div className={`pulse-dot ${statusDot[status]} shrink-0`} />

        {/* Entry ID */}
        <div className="w-20 shrink-0">
          <span className="mono text-sm font-semibold text-[var(--text-primary)]">
            {result.entry_id}
          </span>
        </div>

        {/* Description */}
        <div className="flex-1 min-w-0">
          <p className="text-sm text-[var(--text-secondary)] truncate">
            {entry.description}
          </p>
        </div>

        {/* Date */}
        <div className="hidden md:block w-24 shrink-0 text-right">
          <span className="text-xs text-[var(--text-muted)] mono">
            {entry.entry_date}
          </span>
        </div>

        {/* Debit / Credit */}
        <div className="hidden lg:block w-28 shrink-0 text-right">
          <span className="text-xs text-[var(--text-muted)] mono">
            {fmt(entry.lines.reduce((s, l) => s + parseFloat(l.debit), 0))}
          </span>
        </div>

        {/* Status Badge */}
        <div className="w-28 shrink-0 flex justify-end">
          <span className={`badge ${statusBadge[status]}`}>
            {status}
          </span>
        </div>

        {/* Chevron */}
        <div className="w-5 shrink-0 flex justify-center">
          <svg
            className={`w-4 h-4 text-[var(--text-muted)] transition-transform duration-200 ${open ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* ── Expandable Detail Panel ───────────────────────────────────── */}
      <div className={`detail-panel ${open ? "detail-panel-open" : "detail-panel-closed"}`}>
        <div className="px-4 md:px-6 pb-5 pt-2 border-b border-[var(--border-subtle)]">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

            {/* LEFT: Checks + Line Items */}
            <div className="space-y-4">
              {/* Trace + Meta */}
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--text-muted)]">
                <span>Trace: <span className="mono text-[var(--text-secondary)]">{result.trace_id}</span></span>
                <span>Pipeline: <span className="mono text-[var(--text-secondary)]">v{result.pipeline_version}</span></span>
                {entry.prepared_by && <span>By: <span className="text-[var(--text-secondary)]">{entry.prepared_by}</span></span>}
                {entry.approved_by && <span>Approved: <span className="text-[var(--text-secondary)]">{entry.approved_by}</span></span>}
              </div>

              {/* Tags */}
              {entry.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {entry.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-[rgba(59,130,246,0.1)] text-[var(--accent-blue)] border border-[rgba(59,130,246,0.2)]"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {/* Check Results */}
              <div>
                <h4 className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider mb-2">
                  Validation Checks
                </h4>
                <div className="space-y-1.5">
                  {result.checks_run.map((check: CheckResult, ci: number) => (
                    <div
                      key={ci}
                      className="flex items-center gap-2 text-xs"
                    >
                      {check.passed ? (
                        <svg className="w-3.5 h-3.5 text-[var(--status-accepted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      ) : (
                        <svg className="w-3.5 h-3.5 text-[var(--status-rejected)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      )}
                      <span className={`mono font-medium ${check.passed ? "text-[var(--text-muted)]" : "text-[var(--status-rejected)]"}`}>
                        {check.check}
                      </span>
                      {check.error_code && (
                        <span className="text-[var(--text-muted)]">— {check.error_code}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Line Items Table */}
              <div>
                <h4 className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider mb-2">
                  Journal Lines
                </h4>
                <div className="overflow-x-auto rounded-lg border border-[var(--border-subtle)]">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-[rgba(148,163,184,0.04)]">
                        <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">#</th>
                        <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Account</th>
                        <th className="px-3 py-2 text-right text-[var(--text-muted)] font-medium">Debit</th>
                        <th className="px-3 py-2 text-right text-[var(--text-muted)] font-medium">Credit</th>
                        <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Memo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entry.lines.map((line) => (
                        <tr key={line.line_number} className="border-t border-[var(--border-subtle)]">
                          <td className="px-3 py-2 mono text-[var(--text-muted)]">{line.line_number}</td>
                          <td className="px-3 py-2 mono font-medium text-[var(--text-primary)]">{line.account_code}</td>
                          <td className="px-3 py-2 text-right mono text-[var(--status-accepted)]">
                            {parseFloat(line.debit) > 0 ? fmt(line.debit) : "—"}
                          </td>
                          <td className="px-3 py-2 text-right mono text-[var(--status-rejected)]">
                            {parseFloat(line.credit) > 0 ? fmt(line.credit) : "—"}
                          </td>
                          <td className="px-3 py-2 text-[var(--text-muted)] max-w-48 truncate">{line.memo || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* RIGHT: LLM Explanation */}
            <div className="space-y-4">
              {explanation ? (
                <>
                  {/* Summary */}
                  <div className="glass-card p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <svg className="w-4 h-4 text-[var(--accent-purple)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                      </svg>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--accent-purple)]">
                        AI Explanation
                      </h4>
                      <span className={`badge text-[10px] py-0 px-2 ${severityColor[explanation.severity]}`}>
                        {explanation.severity}
                      </span>
                    </div>
                    <p className="text-sm font-medium text-[var(--text-primary)] mb-2">
                      {explanation.summary}
                    </p>
                    <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                      {explanation.detail}
                    </p>
                  </div>

                  {/* Suggested Correction */}
                  {explanation.suggested_correction && (
                    <div className="p-3 rounded-lg border border-[rgba(59,130,246,0.2)] bg-[rgba(59,130,246,0.05)]">
                      <div className="flex items-center gap-1.5 mb-1">
                        <svg className="w-3.5 h-3.5 text-[var(--accent-blue)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent-blue)]">
                          Suggested Fix
                          {explanation.llm_suggestion_verified && (
                            <span className="ml-1 text-[var(--status-accepted)]">✓ Verified</span>
                          )}
                        </span>
                      </div>
                      <p className="text-xs text-[var(--text-secondary)]">{explanation.suggested_correction}</p>
                    </div>
                  )}

                  {/* Risk Flags */}
                  {explanation.risk_flags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {explanation.risk_flags.map((flag) => (
                        <span
                          key={flag}
                          className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-[var(--status-quarantined-bg)] text-[var(--status-quarantined)] border border-[var(--status-quarantined-border)]"
                        >
                          ⚠ {flag}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* LLM Meta */}
                  {result.llm_model && (
                    <div className="text-[10px] text-[var(--text-muted)] flex gap-3">
                      <span>Model: <span className="mono">{result.llm_model}</span></span>
                      {result.llm_latency_ms !== null && (
                        <span>Latency: <span className="mono">{result.llm_latency_ms}ms</span></span>
                      )}
                    </div>
                  )}
                </>
              ) : status === "QUARANTINED" ? (
                <div className="glass-card p-4 border-[var(--status-quarantined-border)]">
                  <div className="flex items-center gap-2 mb-2">
                    <svg className="w-4 h-4 text-[var(--status-quarantined)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
                    </svg>
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--status-quarantined)]">
                      Requires Human Review
                    </h4>
                  </div>
                  <p className="text-xs text-[var(--text-secondary)]">
                    This entry failed validation but the AI explainer could not generate an explanation
                    (status: <span className="mono font-medium">{result.llm_status}</span>).
                    A senior accountant must review this entry manually.
                  </p>
                </div>
              ) : (
                <div className="glass-card p-4 border-[var(--status-accepted-border)]">
                  <div className="flex items-center gap-2">
                    <svg className="w-4 h-4 text-[var(--status-accepted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span className="text-xs text-[var(--status-accepted)] font-medium">
                      All validation checks passed. No issues detected.
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
