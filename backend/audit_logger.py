"""
audit_logger.py — Append-Only Audit Log Writer
================================================
Writes one JSONL record per entry to validation_log.jsonl.

Rules:
  - NEVER overwrites existing records. Always appends.
  - Flushes after every write (no buffered loss on crash).
  - AuditLogRecord.from_validation_result() is the only constructor used.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.models import AuditLogRecord, ValidationResult

log = logging.getLogger("maa.audit_logger")


class AuditLogger:
    """
    Thread-safe append-only writer for validation_log.jsonl.

    Usage:
        logger = AuditLogger("output/validation_log.jsonl", run_id="RUN-001")
        logger.write(validation_result)
    """

    def __init__(self, log_path: str | Path, run_id: str) -> None:
        self.log_path = Path(log_path)
        self.run_id = run_id
        # Ensure output directory exists.
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("AuditLogger initialised → %s (run_id=%s)", self.log_path, run_id)

    def write(self, result: ValidationResult) -> None:
        """
        Promote a ValidationResult to an AuditLogRecord and append to JSONL.
        Flushes immediately to prevent data loss on unexpected exits.
        """
        record = AuditLogRecord.from_validation_result(result, run_id=self.run_id)
        line = record.model_dump_json() + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        log.debug("Audit record written: trace_id=%s status=%s", record.trace_id, record.status)
