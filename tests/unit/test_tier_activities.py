from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.tiers.activities import (
    AcquireActionPermitInput,
    AcquireWorkflowPermitInput,
    acquire_action_permit_activity,
    acquire_workflow_permit_activity,
)
from tracecat.tiers.exceptions import InvalidOrganizationConcurrencyCapError
from tracecat.tiers.permits import PermitAcquireOutcome
from tracecat.tiers.semaphore import AcquireResult

ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000111")


@pytest.mark.anyio
async def test_acquire_action_permit_activity_uses_permit_service() -> None:
    permit_svc = SimpleNamespace(
        acquire_action_permit=AsyncMock(
            return_value=PermitAcquireOutcome(
                result=AcquireResult(acquired=True, current_count=1),
                effective_limit=2,
                cap_source="cache",
            )
        )
    )

    with patch(
        "tracecat.tiers.activities.TierPermitService.create",
        new=AsyncMock(return_value=permit_svc),
    ):
        result = await acquire_action_permit_activity(
            AcquireActionPermitInput(org_id=ORG_ID, action_id="wf:root:task", limit=99)
        )

    assert result.acquired is True
    permit_svc.acquire_action_permit.assert_awaited_once_with(
        org_id=ORG_ID,
        action_id="wf:root:task",
    )


@pytest.mark.anyio
async def test_acquire_workflow_permit_activity_maps_invalid_cap_error() -> None:
    permit_svc = SimpleNamespace(
        acquire_workflow_permit=AsyncMock(
            side_effect=InvalidOrganizationConcurrencyCapError(
                scope="workflow",
                org_id=ORG_ID,
                limit=0,
            )
        )
    )

    with patch(
        "tracecat.tiers.activities.TierPermitService.create",
        new=AsyncMock(return_value=permit_svc),
    ):
        with pytest.raises(
            ApplicationError,
            match="Invalid organization concurrency cap",
        ) as exc_info:
            await acquire_workflow_permit_activity(
                AcquireWorkflowPermitInput(
                    org_id=ORG_ID,
                    workflow_id="wf-exec",
                    limit=99,
                )
            )

    assert exc_info.value.type == "InvalidOrganizationConcurrencyCap"
