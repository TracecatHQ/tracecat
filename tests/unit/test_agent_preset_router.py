"""Validation error contracts for agent preset routes."""

import uuid
from typing import get_args
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.router import (
    compare_agent_preset_versions,
    create_agent_preset,
    get_agent_preset_version,
    list_agent_preset_versions,
    preview_tool_policy,
    restore_agent_preset_version,
    router,
    update_agent_preset,
)
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetToolPolicyPreview,
    AgentPresetUpdate,
    AgentPresetValidationErrorResponse,
)
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.exceptions import TracecatValidationError


@pytest.mark.parametrize(
    "detail",
    [
        {"code": "agent_tool_limit_exceeded", "tool_count": 140, "max_tools": 128},
        "Agent preset slug cannot be empty",
    ],
)
def test_preset_validation_error_response_contract(
    detail: dict[str, str | int] | str,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:create"}),
    )
    app = FastAPI()
    app.include_router(router)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: role
    app.dependency_overrides[get_async_session] = lambda: AsyncMock(spec=AsyncSession)
    with (
        patch(
            "tracecat.agent.preset.router.AgentPresetService.create_preset",
            new_callable=AsyncMock,
            side_effect=TracecatValidationError(
                "Invalid preset configuration", detail=detail
            ),
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/agent/presets",
            json={
                "name": "Test agent",
                "model_name": "test-model",
                "model_provider": "openai",
            },
        )

    assert response.status_code == 400
    expected = (
        {**detail, "message": "Invalid preset configuration"}
        if isinstance(detail, dict)
        else detail
    )
    assert response.json() == {"detail": expected}
    parsed = AgentPresetValidationErrorResponse.model_validate(response.json())
    assert parsed.model_dump(exclude_unset=True) == response.json()
    error_schema = app.openapi()["paths"]["/agent/presets"]["post"]["responses"]["400"]
    assert error_schema["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AgentPresetValidationErrorResponse"
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "detail",
    [
        {"code": "skill_not_found", "missing_skill_ids": ["synthetic-skill"]},
        {"code": "skill_not_published", "skill_id": "synthetic-skill"},
        {"code": "duplicate_skill_names", "message": "Explicit validation message"},
        "String validation detail",
        None,
    ],
)
async def test_preview_preserves_validation_details(
    detail: dict[str, str | list[str]] | str | None,
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
    expected = detail if detail is not None else "Invalid skill selection"
    if isinstance(detail, dict) and "message" not in detail:
        expected = {**detail, "message": "Invalid skill selection"}
    assert exc_info.value.detail == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation",
    ["create", "update", "restore", "list_versions", "get_version", "compare"],
)
@pytest.mark.parametrize(
    "detail",
    [
        {"code": "agent_tool_limit_exceeded", "tool_count": 140, "max_tools": 128},
        {"code": "duplicate_skill_names", "message": "Explicit validation message"},
        "String validation detail",
        None,
    ],
)
async def test_preset_routes_preserve_validation_details(
    operation: str, detail: dict[str, str | int] | str | None
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:create", "agent:update", "agent:read"}),
    )
    preset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    session = AsyncMock(spec=AsyncSession)
    error = TracecatValidationError("Invalid preset configuration", detail=detail)
    with (
        patch("tracecat.agent.preset.router.AgentPresetService", autospec=True) as cls,
        pytest.raises(HTTPException) as exc_info,
    ):
        service = cls.return_value
        service.get_version.return_value.preset_id = preset_id
        match operation:
            case "create":
                service.create_preset.side_effect = error
                await create_agent_preset(
                    params=AgentPresetCreate(
                        name="Test agent",
                        model_name="test-model",
                        model_provider="openai",
                    ),
                    role=role,
                    session=session,
                )
            case "update":
                service.update_preset.side_effect = error
                await update_agent_preset(
                    preset_id=preset_id,
                    params=AgentPresetUpdate(name="Updated agent"),
                    role=role,
                    session=session,
                )
            case "restore":
                service.restore_version.side_effect = error
                await restore_agent_preset_version(
                    preset_id=preset_id,
                    version_id=version_id,
                    role=role,
                    session=session,
                )
            case "list_versions":
                service.list_versions.side_effect = error
                await list_agent_preset_versions(
                    preset_id=preset_id,
                    limit=20,
                    cursor="invalid-cursor",
                    reverse=False,
                    role=role,
                    session=session,
                )
            case "get_version":
                service.build_version_read.side_effect = error
                await get_agent_preset_version(
                    preset_id=preset_id,
                    version_id=version_id,
                    role=role,
                    session=session,
                )
            case "compare":
                service.compare_versions.side_effect = error
                await compare_agent_preset_versions(
                    preset_id=preset_id,
                    version_id=version_id,
                    compare_to=uuid.uuid4(),
                    role=role,
                    session=session,
                )
    assert exc_info.value.status_code == 400
    expected = detail if detail is not None else "Invalid preset configuration"
    if isinstance(detail, dict) and "message" not in detail:
        expected = {**detail, "message": "Invalid preset configuration"}
    assert exc_info.value.detail == expected
