from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from tracecat.agent.skill.internal_router import list_skill_versions
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


def _executor_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


@pytest.mark.anyio
async def test_list_skill_versions_converts_invalid_cursor_to_http_400() -> None:
    skill_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.get_skill.return_value = object()
    mock_service.list_versions.side_effect = TracecatValidationError(
        "Invalid cursor for skill versions"
    )

    with patch(
        "tracecat.agent.skill.internal_router.SkillService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            raw_list_skill_versions = cast(Any, list_skill_versions).__wrapped__
            await raw_list_skill_versions(
                skill_id=skill_id,
                role=_executor_role(),
                session=AsyncMock(),
                limit=10,
                cursor="not-a-valid-cursor",
                reverse=False,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid cursor for skill versions"
