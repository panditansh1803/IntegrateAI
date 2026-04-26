"""
main.py — MAA Pipeline CLI & FastAPI Entry Point
==================================================
Two modes:
  CLI:   python -m backend.main
  API:   uvicorn backend.main:app --reload

Environment variables:
  LLM_PROVIDER   — "gemini", "openai", or "mock" (default: "mock")
  GOOGLE_API_KEY — Required if LLM_PROVIDER=gemini
  OPENAI_API_KEY — Required if LLM_PROVIDER=openai
  GEMINI_MODEL   — Model name for Gemini (default: gemini-2.0-flash)
  OPENAI_MODEL   — Model name for OpenAI (default: gpt-4o-mini)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from backend.orchestrator import run_pipeline

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING SETUP
# ──────────────────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the MAA pipeline."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI MODE
# ──────────────────────────────────────────────────────────────────────────────

def cli_main() -> None:
    """Run the MAA pipeline from the command line."""
    parser = argparse.ArgumentParser(
        description="Manual Adjustments Agent — Validation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with mock LLM (no API key needed):
  python -m backend.main

  # Run with Google Gemini:
  set GOOGLE_API_KEY=your-key-here
  python -m backend.main --provider gemini

  # Run with OpenAI:
  set OPENAI_API_KEY=your-key-here
  python -m backend.main --provider openai

  # Skip LLM entirely (all failures → QUARANTINED):
  python -m backend.main --skip-llm
        """,
    )
    parser.add_argument(
        "--adjustments", "-a",
        default="data/manual_adjustments.json",
        help="Path to manual_adjustments.json (default: data/manual_adjustments.json)",
    )
    parser.add_argument(
        "--coa", "-c",
        default="data/chart_of_accounts.csv",
        help="Path to chart_of_accounts.csv (default: data/chart_of_accounts.csv)",
    )
    parser.add_argument(
        "--output", "-o",
        default="output/validation_results.json",
        help="Path for validation_results.json output",
    )
    parser.add_argument(
        "--log", "-l",
        default="output/validation_log.jsonl",
        help="Path for validation_log.jsonl audit trail",
    )
    parser.add_argument(
        "--provider", "-p",
        default=None,
        choices=["gemini", "openai", "mock"],
        help="LLM provider (overrides LLM_PROVIDER env var; default: mock)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=10,
        help="LLM call timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip all LLM calls; failed entries → QUARANTINED",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )

    args = parser.parse_args()
    setup_logging("DEBUG" if args.verbose else "INFO")

    summary = run_pipeline(
        adjustments_path=args.adjustments,
        coa_path=args.coa,
        output_results_path=args.output,
        output_log_path=args.log,
        llm_provider=args.provider,
        llm_timeout=args.timeout,
        skip_llm=args.skip_llm,
    )

    # Print final summary to stdout.
    print("\n" + "=" * 60)
    print(f"  MAA Pipeline Complete — {summary.run_id}")
    print("=" * 60)
    print(f"  Total Entries:  {summary.total_entries}")
    print(f"  Accepted:       {summary.accepted}  (green)")
    print(f"  Rejected:       {summary.rejected}  (red)")
    print(f"  Quarantined:    {summary.quarantined}  (yellow)")
    print(f"  Acceptance Rate: {summary.acceptance_rate}%")
    print(f"  COA Accounts:   {summary.coa_account_count}")
    print(f"  IC Check:       {'Active' if summary.ic_check_active else 'Skipped'}")
    if summary.warnings:
        print(f"\n  Warnings ({len(summary.warnings)}):")
        for w in summary.warnings:
            print(f"    - {w}")
    print("=" * 60)

    # Exit with non-zero if there are quarantined entries (needs human attention).
    sys.exit(1 if summary.has_quarantined_entries else 0)


# ──────────────────────────────────────────────────────────────────────────────
# FASTAPI MODE
# ──────────────────────────────────────────────────────────────────────────────

def create_app():
    """Create and configure the FastAPI application."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse

    app = FastAPI(
        title="Manual Adjustments Agent",
        description="ERP-driven journal entry validation pipeline",
        version="1.0.0",
    )

    # CORS — allow the Next.js frontend (default port 3000).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    setup_logging("INFO")

    @app.get("/")
    async def root():
        return {"status": "ok", "service": "Manual Adjustments Agent", "version": "1.0.0"}

    @app.post("/validate")
    async def validate():
        """
        Run the full pipeline and return the PipelineRunSummary.
        Uses mock LLM by default; override with LLM_PROVIDER env var.
        """
        summary = run_pipeline()
        return json.loads(summary.model_dump_json())

    @app.get("/results")
    async def get_results():
        """
        Return the latest validation_results.json if it exists.
        The frontend polls this endpoint.
        """
        results_path = Path("output/validation_results.json")
        if not results_path.exists():
            return {"error": "No results yet. Run POST /validate first."}
        with open(results_path, encoding="utf-8") as f:
            return json.load(f)

    return app


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

# Lazy FastAPI app creation — only instantiated when fastapi is installed.
# This lets `python -m backend.main` work as a CLI without fastapi.
try:
    app = create_app()
except ImportError:
    app = None  # CLI-only mode; FastAPI not installed.

if __name__ == "__main__":
    cli_main()
