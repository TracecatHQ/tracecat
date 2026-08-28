"""Schemas for platform maintenance operations."""

import uuid
from enum import StrEnum

from pydantic import BaseModel


class CaseAgentSessionBackfillStatus(StrEnum):
    """Lifecycle state for the durable backfill operation."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CaseAgentSessionInteractionBackfillStartResponse(BaseModel):
    """Response after starting or joining the durable backfill."""

    operation_id: uuid.UUID


class CaseAgentSessionInteractionBackfillResponse(BaseModel):
    """Aggregate result of the historical interaction backfill."""

    batches_processed: int
    sessions_scanned: int
    history_rows_scanned: int
    mutation_candidates: int
    inserted: int
    existing: int
    skipped: dict[str, int]


class CaseAgentSessionInteractionBackfillStatusResponse(BaseModel):
    """Current state and optional result of the durable backfill."""

    operation_id: uuid.UUID
    status: CaseAgentSessionBackfillStatus
    report: CaseAgentSessionInteractionBackfillResponse | None = None
