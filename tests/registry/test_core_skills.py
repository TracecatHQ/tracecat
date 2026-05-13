from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.skills import (
    get_skill_version,
    list_skill_versions,
    restore_skill_version,
)


@pytest.fixture
async def skill_registry_context() -> AsyncIterator[MagicMock]:
    ctx = MagicMock()
    ctx.agents = MagicMock()
    set_context(cast(RegistryContext, ctx))
    try:
        yield ctx
    finally:
        clear_context()


@pytest.mark.anyio
async def test_list_skill_versions_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    skill_registry_context.agents.list_skill_versions = AsyncMock(
        return_value={"items": [], "next_cursor": None, "has_more": False}
    )

    result = await list_skill_versions(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
        limit=10,
        cursor="cursor-1",
        reverse=True,
    )

    assert result == {"items": [], "next_cursor": None, "has_more": False}
    skill_registry_context.agents.list_skill_versions.assert_awaited_once_with(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
        limit=10,
        cursor="cursor-1",
        reverse=True,
    )


@pytest.mark.anyio
async def test_get_skill_version_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    version_id = uuid.uuid4()
    skill_registry_context.agents.get_skill_version = AsyncMock(
        return_value={"id": "version-id"}
    )

    result = await get_skill_version(
        skill_id="skill-id", skill_uuid=skill_uuid, version_id=version_id
    )

    assert result == {"id": "version-id"}
    skill_registry_context.agents.get_skill_version.assert_awaited_once_with(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
        version_id=version_id,
    )


@pytest.mark.anyio
async def test_restore_skill_version_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    version_id = uuid.uuid4()
    skill_registry_context.agents.restore_skill_version = AsyncMock(
        return_value={"current_version_id": "version-id"}
    )

    result = await restore_skill_version(
        skill_id="skill-id", skill_uuid=skill_uuid, version_id=version_id
    )

    assert result == {"current_version_id": "version-id"}
    skill_registry_context.agents.restore_skill_version.assert_awaited_once_with(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
        version_id=version_id,
    )
