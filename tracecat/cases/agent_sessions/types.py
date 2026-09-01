"""Types for case-agent session interaction backfills."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class CaseAgentSessionBackfillSkipReason(StrEnum):
    """Machine-readable reasons a historical mutation was not recorded."""

    FAILED_TOOL_CALL = "failed_tool_call"
    INCOMPLETE_TOOL_CALL = "incomplete_tool_call"
    UNPARSEABLE_TOOL_CALL = "unparseable_tool_call"
    MISSING_CASE = "missing_case"
    MISSING_COMMENT = "missing_comment"
    INVALID_SESSION_LINEAGE = "invalid_session_lineage"


@dataclass(frozen=True, slots=True)
class CaseAgentSessionBackfillReport:
    """Aggregate, payload-free report for one backfill run."""

    batches_processed: int
    sessions_scanned: int
    history_rows_scanned: int
    mutation_candidates: int
    inserted: int
    existing: int
    skipped: Mapping[CaseAgentSessionBackfillSkipReason, int]
