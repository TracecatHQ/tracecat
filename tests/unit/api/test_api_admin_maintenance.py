"""HTTP coverage for platform maintenance endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.admin.maintenance.router as maintenance_router_module
from tracecat.auth.types import Role
from tracecat.cases.agent_sessions.types import (
    CaseAgentSessionBackfillReport,
    CaseAgentSessionBackfillSkipReason,
)


@pytest.mark.anyio
async def test_case_agent_session_interaction_backfill(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    report = CaseAgentSessionBackfillReport(
        batches_processed=2,
        sessions_scanned=3,
        history_rows_scanned=6,
        mutation_candidates=4,
        inserted=3,
        existing=1,
        skipped={CaseAgentSessionBackfillSkipReason.FAILED_TOOL_CALL: 1},
    )

    with patch.object(
        maintenance_router_module,
        "CaseAgentSessionBackfill",
    ) as backfill_type:
        backfill = AsyncMock()
        backfill.run.return_value = report
        backfill_type.return_value = backfill

        response = client.post(
            "/admin/maintenance/case-agent-session-interactions/backfill"
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "batches_processed": 2,
        "sessions_scanned": 3,
        "history_rows_scanned": 6,
        "mutation_candidates": 4,
        "inserted": 3,
        "existing": 1,
        "skipped": {"failed_tool_call": 1},
    }
    backfill.run.assert_awaited_once_with()
