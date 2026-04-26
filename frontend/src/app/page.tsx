"use client";

import React, { useEffect, useState, useMemo } from "react";
import type { PipelineRunSummary, EntryStatus } from "@/app/types";
import StatsBar from "@/components/StatsBar";
import EntryRow from "@/components/EntryRow";

/**
 * Main dashboard page for the Manual Adjustments Agent.
 *
 * Data source priority:
 *   1. FastAPI backend at http://localhost:8000/results  (if running)
 *   2. Static /validation_results.json from public/     (fallback)
 */

type FilterKey = "ALL" | EntryStatus;

export default function DashboardPage() {
  const [data, setData] = useState<PipelineRunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterKey>("ALL");
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    async function fetchResults() {
      setLoading(true);
      try {
        // Try the FastAPI backend first.
        const apiRes = await fetch("http://localhost:8000/results", { signal: AbortSignal.timeout(2000) });
        if (apiRes.ok) {
          const json = await apiRes.json();
          if (json.results) {
            setData(json as PipelineRunSummary);
            setLoading(false);
            return;
          }
        }
      } catch {
        // Backend not running — fall through to static file.
      }

      try {
        // Fallback: static JSON from public/.
        const staticRes = await fetch("/validation_results.json");
        if (!staticRes.ok) throw new Error(`HTTP ${staticRes.status}`);
        const json = await staticRes.json();
        setData(json as PipelineRunSummary);
      } catch (err) {
        setError("Could not load validation results. Run the pipeline first.");
      } finally {
        setLoading(false);
      }
    }

    fetchResults();
  }, []);

  /** Filtered results based on status tab + search query */
  const filteredResults = useMemo(() => {
    if (!data) return [];
    let results = data.results;

    // Filter by status tab.
    if (filter !== "ALL") {
      results = results.filter((r) => r.status === filter);
    }

    // Filter by search query (entry ID, description, trace ID, account codes).
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      results = results.filter((r) => {
        const entry = r.raw_entry_snapshot;
        return (
          r.entry_id.toLowerCase().includes(q) ||
          r.trace_id.toLowerCase().includes(q) ||
          entry.description.toLowerCase().includes(q) ||
          entry.lines.some((l) => l.account_code.toLowerCase().includes(q)) ||
          r.error_codes.some((c) => c.toLowerCase().includes(q))
        );
      });
    }

    return results;
  }, [data, filter, searchQuery]);

  /** Filter tab definitions */
  const tabs: { key: FilterKey; label: string; count: number }[] = data
    ? [
        { key: "ALL", label: "All Entries", count: data.total_entries },
        { key: "ACCEPTED", label: "Accepted", count: data.accepted },
        { key: "REJECTED", label: "Rejected", count: data.rejected },
        { key: "QUARANTINED", label: "Quarantined", count: data.quarantined },
      ]
    : [];

  const tabColors: Record<FilterKey, string> = {
    ALL: "text-[var(--accent-blue)] border-[var(--accent-blue)]",
    ACCEPTED: "text-[var(--status-accepted)] border-[var(--status-accepted)]",
    REJECTED: "text-[var(--status-rejected)] border-[var(--status-rejected)]",
    QUARANTINED: "text-[var(--status-quarantined)] border-[var(--status-quarantined)]",
  };

  // ── Loading State ──────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center relative z-10">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 mx-auto rounded-full border-2 border-[var(--accent-blue)] border-t-transparent animate-spin" />
          <p className="text-sm text-[var(--text-muted)]">Loading validation results...</p>
        </div>
      </div>
    );
  }

  // ── Error State ────────────────────────────────────────────────────────────
  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center relative z-10">
        <div className="glass-card p-8 max-w-md text-center space-y-3">
          <svg className="w-12 h-12 mx-auto text-[var(--status-rejected)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
          </svg>
          <p className="text-sm text-[var(--text-secondary)]">{error || "No data available"}</p>
          <p className="text-xs text-[var(--text-muted)]">
            Run <code className="mono bg-[var(--bg-elevated)] px-1.5 py-0.5 rounded">python -m backend.main</code> to generate results.
          </p>
        </div>
      </div>
    );
  }

  // ── Main Dashboard ─────────────────────────────────────────────────────────
  return (
    <main className="min-h-screen relative z-10">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="border-b border-[var(--border-subtle)] bg-[rgba(10,14,26,0.8)] backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 md:px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            {/* Logo */}
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-[var(--accent-blue)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </div>
            <div>
              <h1 className="text-base font-bold text-[var(--text-primary)]">
                Manual Adjustments Agent
              </h1>
              <p className="text-[10px] text-[var(--text-muted)] mono">
                {data.run_id} · v{data.pipeline_version}
              </p>
            </div>
          </div>

          {/* Pipeline info */}
          <div className="hidden md:flex items-center gap-4 text-xs text-[var(--text-muted)]">
            <span>COA: <span className="mono text-[var(--text-secondary)]">{data.coa_account_count} accounts</span></span>
            <span className="w-px h-4 bg-[var(--border-default)]" />
            <span>IC Check: <span className={`font-medium ${data.ic_check_active ? "text-[var(--status-accepted)]" : "text-[var(--status-quarantined)]"}`}>
              {data.ic_check_active ? "Active" : "Skipped"}
            </span></span>
            {data.completed_at && (
              <>
                <span className="w-px h-4 bg-[var(--border-default)]" />
                <span>{new Date(data.completed_at).toLocaleString()}</span>
              </>
            )}
          </div>
        </div>
      </header>

      {/* ── Content ────────────────────────────────────────────────────── */}
      <div className="max-w-7xl mx-auto px-4 md:px-6 py-6 space-y-6">
        {/* Stats Cards */}
        <StatsBar data={data} />

        {/* Warnings */}
        {data.warnings.length > 0 && (
          <div className="glass-card p-4 border-[var(--status-quarantined-border)] animate-fade-in-up stagger-4">
            <div className="flex items-center gap-2 mb-2">
              <svg className="w-4 h-4 text-[var(--status-quarantined)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--status-quarantined)]">
                Pipeline Warnings ({data.warnings.length})
              </span>
            </div>
            {data.warnings.map((w, i) => (
              <p key={i} className="text-xs text-[var(--text-secondary)] ml-6 mb-0.5">{w}</p>
            ))}
          </div>
        )}

        {/* Filter Tabs + Search */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-0 sm:justify-between">
          {/* Tabs */}
          <div className="flex gap-1 p-1 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-subtle)]">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                id={`filter-tab-${tab.key.toLowerCase()}`}
                onClick={() => setFilter(tab.key)}
                className={`
                  px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200
                  ${filter === tab.key
                    ? `bg-[var(--bg-glass-hover)] ${tabColors[tab.key]} border-b-2`
                    : "text-[var(--text-muted)] hover:text-[var(--text-secondary)] border-b-2 border-transparent"
                  }
                `}
              >
                {tab.label}
                <span className="ml-1.5 mono text-[10px] opacity-70">{tab.count}</span>
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="relative">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              id="search-input"
              type="text"
              placeholder="Search entries, accounts, errors..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="
                w-full sm:w-64 pl-9 pr-3 py-2 rounded-lg text-xs
                bg-[var(--bg-elevated)] border border-[var(--border-subtle)]
                text-[var(--text-primary)] placeholder-[var(--text-muted)]
                focus:outline-none focus:border-[var(--accent-blue)] focus:ring-1 focus:ring-[var(--accent-blue)]
                transition-all duration-200
              "
            />
          </div>
        </div>

        {/* Results Table */}
        <div className="glass-card overflow-hidden">
          {/* Table Header */}
          <div className="px-4 md:px-6 py-3 flex items-center gap-3 md:gap-5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] border-b border-[var(--border-subtle)] bg-[rgba(148,163,184,0.03)]">
            <div className="w-[8px] shrink-0" /> {/* dot spacer */}
            <div className="w-20 shrink-0">Entry ID</div>
            <div className="flex-1">Description</div>
            <div className="hidden md:block w-24 shrink-0 text-right">Date</div>
            <div className="hidden lg:block w-28 shrink-0 text-right">Total Debit</div>
            <div className="w-28 shrink-0 text-right">Status</div>
            <div className="w-5 shrink-0" /> {/* chevron spacer */}
          </div>

          {/* Rows */}
          {filteredResults.length === 0 ? (
            <div className="px-6 py-12 text-center">
              <p className="text-sm text-[var(--text-muted)]">
                {searchQuery ? "No entries match your search." : "No entries in this category."}
              </p>
            </div>
          ) : (
            filteredResults.map((result, idx) => (
              <EntryRow key={`${result.trace_id}`} result={result} index={idx} />
            ))
          )}
        </div>

        {/* Footer */}
        <footer className="text-center text-[10px] text-[var(--text-muted)] py-4 space-y-1">
          <p>Manual Adjustments Agent · Pipeline v{data.pipeline_version}</p>
          <p>
            Audit log: <span className="mono">validation_log.jsonl</span> ·
            Output: <span className="mono">validation_results.json</span>
          </p>
        </footer>
      </div>
    </main>
  );
}
