"use client";

import React from "react";
import type { PipelineRunSummary } from "@/app/types";

/**
 * StatsBar — Top-level metric cards showing pipeline run summary.
 * Four glassmorphic cards: Total, Accepted, Rejected, Quarantined.
 */
export default function StatsBar({ data }: { data: PipelineRunSummary }) {
  const acceptRate = data.total_entries > 0
    ? ((data.accepted / data.total_entries) * 100).toFixed(1)
    : "0.0";

  const cards = [
    {
      label: "Total Entries",
      value: data.total_entries,
      sub: `Run: ${data.run_id}`,
      variant: "total" as const,
      icon: (
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      ),
    },
    {
      label: "Accepted",
      value: data.accepted,
      sub: `${acceptRate}% acceptance rate`,
      variant: "accepted" as const,
      icon: (
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
      ),
    },
    {
      label: "Rejected",
      value: data.rejected,
      sub: `${data.rejected} need correction`,
      variant: "rejected" as const,
      icon: (
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      ),
    },
    {
      label: "Quarantined",
      value: data.quarantined,
      sub: data.quarantined > 0 ? "Needs human review" : "None pending",
      variant: "quarantined" as const,
      icon: (
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
      ),
    },
  ];

  const colorMap = {
    total: "text-[var(--accent-blue)]",
    accepted: "text-[var(--status-accepted)]",
    rejected: "text-[var(--status-rejected)]",
    quarantined: "text-[var(--status-quarantined)]",
  };

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-5">
      {cards.map((card, idx) => (
        <div
          key={card.label}
          id={`stat-card-${card.variant}`}
          className={`stat-card stat-card-${card.variant} animate-fade-in-up stagger-${idx + 1}`}
        >
          <div className="relative z-10">
            {/* Header row */}
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium tracking-wider uppercase text-[var(--text-muted)]">
                {card.label}
              </span>
              <span className={colorMap[card.variant]}>
                {card.icon}
              </span>
            </div>

            {/* Big number */}
            <div className={`text-3xl font-bold mb-1 mono ${colorMap[card.variant]}`}>
              {card.value}
            </div>

            {/* Subtitle */}
            <div className="text-xs text-[var(--text-muted)] truncate">
              {card.sub}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
