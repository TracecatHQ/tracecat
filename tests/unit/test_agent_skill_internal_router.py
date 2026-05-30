from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from tracecat.agent.skill.internal_router import (
    get_skill,
    get_skill_version,
    list_skill_versions,
    list_skills,
    publish_skill_version,
    router,
)
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


def test_internal_router_does_not_expose_draft_publish() -> None:
    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert ("/internal/agent/skills/{skill_id}/publish", "POST") not in routes
    assert ("/internal/agent/skills/{skill_id}/versions", "POST") in routes


@pytest.mark.anyio
async def test_list_skills_converts_invalid_cursor_to_http_400() -> None:
    mock_service = AsyncMock()
    mock_service.list_skills.side_effect = TracecatValidationError(
        "Invalid cursor for skills"
    )

    with patch(
        "tracecat.agent.skill.internal_router.SkillService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            raw_list_skills = cast(Any, list_skills).__wrapped__
            await raw_list_skills(
                role=_executor_role(),
                session=AsyncMock(),
                limit=10,
                cursor="not-a-valid-cursor",
                reverse=False,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid cursor for skills"


@pytest.mark.anyio
async def test_list_skill_versions_converts_invalid_cursor_to_http_400() -> None:
    skill_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.get_skill_by_identifier.return_value = SimpleNamespace(id=skill_id)
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


@pytest.mark.anyio
async def test_get_skill_resolves_slug_identifier() -> None:
    resolved_skill_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.get_skill_by_identifier.return_value = SimpleNamespace(
        id=resolved_skill_id
    )
    mock_service.get_skill_read.return_value = {"id": str(resolved_skill_id)}

    with patch(
        "tracecat.agent.skill.internal_router.SkillService",
        return_value=mock_service,
    ):
        raw_get_skill = cast(Any, get_skill).__wrapped__
        result = await raw_get_skill(
            skill_id="triage-helper",
            role=_executor_role(),
            session=AsyncMock(),
        )

    assert result == {"id": str(resolved_skill_id)}
    mock_service.get_skill_by_identifier.assert_awaited_once_with("triage-helper")
    mock_service.get_skill_read.assert_awaited_once_with(resolved_skill_id)


@pytest.mark.anyio
async def test_get_skill_version_returns_snapshot_for_udfs() -> None:
    resolved_skill_id = uuid.uuid4()
    version_id = uuid.uuid4()
    snapshot = {"id": str(version_id), "files": [{"content_base64": "dmVyc2lvbg=="}]}
    mock_service = AsyncMock()
    mock_service.get_skill_by_identifier.return_value = SimpleNamespace(
        id=resolved_skill_id
    )
    mock_service.get_version_snapshot_read.return_value = snapshot

    with patch(
        "tracecat.agent.skill.internal_router.SkillService",
        return_value=mock_service,
    ):
        raw_get_skill_version = cast(Any, get_skill_version).__wrapped__
        result = await raw_get_skill_version(
            skill_id="triage-helper",
            version_id=version_id,
            role=_executor_role(),
            session=AsyncMock(),
        )

    assert result == snapshot
    mock_service.get_skill_by_identifier.assert_awaited_once_with("triage-helper")
    mock_service.get_version_snapshot_read.assert_awaited_once_with(
        skill_id=resolved_skill_id,
        version_id=version_id,
    )


@pytest.mark.anyio
async def test_publish_skill_version_converts_version_conflict_to_http_409() -> None:
    current_version_id = uuid.uuid4()
    detail = {
        "code": "skill_version_conflict",
        "current_version_id": str(current_version_id),
    }
    mock_service = AsyncMock()
    mock_service.get_skill_by_identifier.return_value = SimpleNamespace(id=uuid.uuid4())
    mock_service.publish_skill_version.side_effect = TracecatValidationError(
        "Skill version conflict",
        detail=detail,
    )

    with patch(
        "tracecat.agent.skill.internal_router.SkillService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            raw_publish_skill_version = cast(Any, publish_skill_version).__wrapped__
            await raw_publish_skill_version(
                skill_id=uuid.uuid4(),
                params=cast(Any, object()),
                role=_executor_role(),
                session=AsyncMock(),
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == detail
