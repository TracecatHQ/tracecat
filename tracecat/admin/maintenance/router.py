"""Superuser-only platform maintenance endpoints."""

from fastapi import APIRouter

from tracecat.admin.maintenance.schemas import (
    CaseAgentSessionInteractionBackfillResponse,
)
from tracecat.auth.credentials import SuperuserRole
from tracecat.cases.agent_sessions.backfill import (
    CaseAgentSessionBackfill,
)
from tracecat.db.dependencies import AsyncDBSessionBypass

router = APIRouter(prefix="/maintenance", tags=["admin:maintenance"])


@router.post(
    "/case-agent-session-interactions/backfill",
    response_model=CaseAgentSessionInteractionBackfillResponse,
)
async def backfill_case_agent_session_interactions(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> CaseAgentSessionInteractionBackfillResponse:
    """Backfill successful historical agent-driven case mutations."""
    report = await CaseAgentSessionBackfill(session).run()
    return CaseAgentSessionInteractionBackfillResponse.model_validate(
        report,
        from_attributes=True,
    )
