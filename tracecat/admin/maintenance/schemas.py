"""Schemas for platform maintenance operations."""

from pydantic import BaseModel


class CaseAgentSessionInteractionBackfillResponse(BaseModel):
    """Aggregate result of the historical interaction backfill."""

    batches_processed: int
    sessions_scanned: int
    history_rows_scanned: int
    mutation_candidates: int
    inserted: int
    existing: int
    skipped: dict[str, int]
