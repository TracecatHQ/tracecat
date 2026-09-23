from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.skills import (
    get_skill_draft,
    get_skill_draft_file,
    get_skill_version,
    list_skill_versions,
    publish_skill_draft,
    restore_skill_version,
    update_skill,
    update_skill_draft,
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


@pytest.mark.anyio
async def test_get_skill_draft_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    skill_registry_context.agents.get_skill_draft = AsyncMock(
        return_value={"revision": 1}
    )

    result = await get_skill_draft("skill-id", skill_uuid=skill_uuid)

    assert result == {"revision": 1}
    skill_registry_context.agents.get_skill_draft.assert_awaited_once_with(
        "skill-id", skill_uuid=skill_uuid
    )


@pytest.mark.anyio
async def test_get_skill_draft_file_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    skill_registry_context.agents.get_skill_draft_file = AsyncMock(
        return_value={"path": "SKILL.md"}
    )

    result = await get_skill_draft_file(
        "skill-id", path="SKILL.md", skill_uuid=skill_uuid
    )

    assert result == {"path": "SKILL.md"}
    skill_registry_context.agents.get_skill_draft_file.assert_awaited_once_with(
        skill_id="skill-id",
        path="SKILL.md",
        skill_uuid=skill_uuid,
    )


@pytest.mark.anyio
async def test_update_skill_draft_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    operations = [{"op": "upsert_text_file", "path": "SKILL.md", "content": "draft"}]
    skill_registry_context.agents.patch_skill_draft = AsyncMock(
        return_value={"revision": 2}
    )

    result = await update_skill_draft(
        "skill-id",
        base_revision=1,
        operations=operations,
        skill_uuid=skill_uuid,
    )

    assert result == {"revision": 2}
    skill_registry_context.agents.patch_skill_draft.assert_awaited_once_with(
        skill_id="skill-id",
        base_revision=1,
        operations=operations,
        skill_uuid=skill_uuid,
    )


@pytest.mark.anyio
async def test_publish_skill_draft_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    skill_registry_context.agents.publish_skill_draft = AsyncMock(
        return_value={"id": "version-id"}
    )

    result = await publish_skill_draft("skill-id", skill_uuid=skill_uuid)

    assert result == {"id": "version-id"}
    skill_registry_context.agents.publish_skill_draft.assert_awaited_once_with(
        "skill-id", skill_uuid=skill_uuid
    )


@pytest.mark.anyio
async def test_update_skill_merges_current_version_files(
    skill_registry_context: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    skill_registry_context.agents.get_skill = AsyncMock(
        return_value={"current_version_id": "version-id"}
    )
    skill_registry_context.agents.get_skill_version = AsyncMock(
        return_value={
            "files": [
                {
                    "path": "SKILL.md",
                    "content_base64": "b2xk",
                    "content_type": "text/markdown",
                },
                {
                    "path": "README.md",
                    "content_base64": "cmVhZG1l",
                    "content_type": "text/markdown",
                },
            ]
        }
    )
    skill_registry_context.agents.publish_skill_version = AsyncMock(
        return_value={"id": "new-version"}
    )
    replacement = {
        "path": "SKILL.md",
        "content_base64": "bmV3",
        "content_type": "text/markdown",
    }

    result = await update_skill(
        "skill-id",
        files=[replacement],
        delete_paths=["README.md"],
        skill_uuid=skill_uuid,
    )

    assert result == {"id": "new-version"}
    skill_registry_context.agents.get_skill.assert_awaited_once_with(
        "skill-id", skill_uuid=skill_uuid
    )
    skill_registry_context.agents.get_skill_version.assert_awaited_once_with(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
        version_id="version-id",
    )
    skill_registry_context.agents.publish_skill_version.assert_awaited_once_with(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
        base_version_id="version-id",
        files=[replacement],
    )


@pytest.mark.anyio
async def test_update_skill_without_current_version_skips_snapshot(
    skill_registry_context: MagicMock,
) -> None:
    skill_registry_context.agents.get_skill = AsyncMock(
        return_value={"current_version_id": None}
    )
    skill_registry_context.agents.get_skill_version = AsyncMock()
    skill_registry_context.agents.publish_skill_version = AsyncMock(
        return_value={"id": "first-version"}
    )
    files = [
        {
            "path": "SKILL.md",
            "content_base64": "bmV3",
            "content_type": "text/markdown",
        }
    ]

    result = await update_skill("skill-id", files=files)

    assert result == {"id": "first-version"}
    skill_registry_context.agents.get_skill_version.assert_not_awaited()
    skill_registry_context.agents.publish_skill_version.assert_awaited_once_with(
        skill_id="skill-id",
        skill_uuid=None,
        base_version_id=None,
        files=files,
    )


@pytest.mark.anyio
async def test_update_skill_rejects_empty_result(
    skill_registry_context: MagicMock,
) -> None:
    skill_registry_context.agents.get_skill = AsyncMock(
        return_value={"current_version_id": "version-id"}
    )
    skill_registry_context.agents.get_skill_version = AsyncMock(
        return_value={
            "files": [
                {
                    "path": "SKILL.md",
                    "content_base64": "b2xk",
                    "content_type": "text/markdown",
                }
            ]
        }
    )
    skill_registry_context.agents.publish_skill_version = AsyncMock()

    with pytest.raises(ValueError, match="Skill update must leave at least one file"):
        await update_skill("skill-id", delete_paths=["SKILL.md"])

    skill_registry_context.agents.publish_skill_version.assert_not_awaited()
