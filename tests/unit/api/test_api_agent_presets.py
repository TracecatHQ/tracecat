"""HTTP-level tests for agent preset API endpoints."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from tracecat.agent.preset import router as agent_preset_router
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


@pytest.mark.anyio
async def test_restore_agent_preset_version_maps_validation_error(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation failures during restore should remain client errors."""

    preset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    service = AsyncMock()
    service.get_preset.return_value = SimpleNamespace(id=preset_id)
    service.get_version.return_value = SimpleNamespace(
        id=version_id,
        preset_id=preset_id,
    )
    service.restore_version.side_effect = TracecatValidationError(
        "Skill binding could not be restored"
    )
    monkeypatch.setattr(
        agent_preset_router,
        "AgentPresetService",
        lambda *args, **kwargs: service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await agent_preset_router.restore_agent_preset_version(
            preset_id=preset_id,
            version_id=version_id,
            role=test_admin_role,
            session=AsyncMock(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Skill binding could not be restored"
    service.restore_version.assert_awaited_once()
