from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.skills import (
    delete_skill,
    get_skill,
    list_skill_versions,
    publish_skill,
    restore_skill_version,
    update_skill,
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
async def test_get_skill_returns_skill_with_draft_files(
    skill_registry_context: MagicMock,
) -> None:
    files = [{"path": "SKILL.md", "sha256": "abc", "size_bytes": 3}]
    skill_registry_context.agents.get_skill = AsyncMock(
        return_value={"slug": "triage", "current_version_id": "version-id"}
    )
    skill_registry_context.agents.get_skill_draft = AsyncMock(
        return_value={"draft_revision": 2, "files": files}
    )

    result = await get_skill("triage")

    assert result == {
        "slug": "triage",
        "current_version_id": "version-id",
        "draft_files": files,
    }
    skill_registry_context.agents.get_skill.assert_awaited_once_with("triage")
    skill_registry_context.agents.get_skill_draft.assert_awaited_once_with("triage")


@pytest.mark.anyio
async def test_get_skill_with_path_reads_draft_file(
    skill_registry_context: MagicMock,
) -> None:
    file = {"kind": "inline", "path": "SKILL.md", "text_content": "# Triage"}
    skill_registry_context.agents.get_skill_draft_file = AsyncMock(return_value=file)
    skill_registry_context.agents.get_skill = AsyncMock()

    result = await get_skill("triage", path="SKILL.md")

    assert result == file
    skill_registry_context.agents.get_skill_draft_file.assert_awaited_once_with(
        skill_id="triage", path="SKILL.md"
    )
    skill_registry_context.agents.get_skill.assert_not_awaited()


@pytest.mark.anyio
async def test_update_skill_uses_given_base_revision(
    skill_registry_context: MagicMock,
) -> None:
    operations = [{"op": "upsert_text_file", "path": "SKILL.md", "content": "draft"}]
    skill_registry_context.agents.get_skill_draft = AsyncMock()
    skill_registry_context.agents.patch_skill_draft = AsyncMock(
        return_value={"draft_revision": 2}
    )

    result = await update_skill("triage", operations=operations, base_revision=1)

    assert result == {"draft_revision": 2}
    skill_registry_context.agents.get_skill_draft.assert_not_awaited()
    skill_registry_context.agents.patch_skill_draft.assert_awaited_once_with(
        skill_id="triage", base_revision=1, operations=operations
    )


@pytest.mark.anyio
async def test_update_skill_defaults_to_latest_draft_revision(
    skill_registry_context: MagicMock,
) -> None:
    operations = [{"op": "delete_file", "path": "notes.md"}]
    skill_registry_context.agents.get_skill_draft = AsyncMock(
        return_value={"draft_revision": 5, "files": []}
    )
    skill_registry_context.agents.patch_skill_draft = AsyncMock(
        return_value={"draft_revision": 6}
    )

    await update_skill("triage", operations=operations)

    skill_registry_context.agents.patch_skill_draft.assert_awaited_once_with(
        skill_id="triage", base_revision=5, operations=operations
    )


@pytest.mark.anyio
async def test_publish_skill_publishes_draft(
    skill_registry_context: MagicMock,
) -> None:
    skill_registry_context.agents.publish_skill_draft = AsyncMock(
        return_value={"id": "version-id"}
    )

    assert await publish_skill("triage") == {"id": "version-id"}
    skill_registry_context.agents.publish_skill_draft.assert_awaited_once_with("triage")


@pytest.mark.anyio
async def test_delete_skill_archives_skill(
    skill_registry_context: MagicMock,
) -> None:
    skill_registry_context.agents.archive_skill = AsyncMock(return_value=None)

    await delete_skill("triage")

    skill_registry_context.agents.archive_skill.assert_awaited_once_with("triage")


@pytest.mark.anyio
async def test_list_skill_versions_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    page = {"items": [], "next_cursor": None, "has_more": False}
    skill_registry_context.agents.list_skill_versions = AsyncMock(return_value=page)

    result = await list_skill_versions(
        "triage", limit=10, cursor="cursor-1", reverse=True
    )

    assert result == page
    skill_registry_context.agents.list_skill_versions.assert_awaited_once_with(
        skill_id="triage", limit=10, cursor="cursor-1", reverse=True
    )


@pytest.mark.anyio
async def test_restore_skill_version_delegates_to_agents_sdk(
    skill_registry_context: MagicMock,
) -> None:
    version_id = uuid.uuid4()
    skill_registry_context.agents.restore_skill_version = AsyncMock(
        return_value={"current_version_id": str(version_id)}
    )

    result = await restore_skill_version("triage", version_id=version_id)

    assert result == {"current_version_id": str(version_id)}
    skill_registry_context.agents.restore_skill_version.assert_awaited_once_with(
        skill_id="triage", version_id=version_id
    )
