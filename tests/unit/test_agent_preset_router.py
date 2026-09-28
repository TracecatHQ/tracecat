"""Validation error contracts for the preset policy preview."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.router import preview_tool_policy
from tracecat.agent.preset.schemas import AgentPresetToolPolicyPreview
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


@pytest.mark.anyio
@pytest.mark.parametrize(
    "detail",
    [
        {"code": "skill_not_found", "missing_skill_ids": ["synthetic-skill"]},
        {"code": "skill_not_published", "skill_id": "synthetic-skill"},
        None,
    ],
)
async def test_preview_preserves_validation_details(
    detail: dict[str, str | list[str]] | None,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )
    error = TracecatValidationError("Invalid skill selection", detail=detail)
    with (
        patch(
            "tracecat.agent.preset.router.AgentPresetService.preview_tool_policy",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await preview_tool_policy(
            params=AgentPresetToolPolicyPreview(),
            role=role,
            session=AsyncMock(spec=AsyncSession),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        detail if detail is not None else "Invalid skill selection"
    )
