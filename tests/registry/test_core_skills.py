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

from tests._registry_utils import core_udfs, input_schema_for

SKILL_UUID = uuid.uuid4()
FILES = [{"path": "SKILL.md", "content_base64": "IyBUcmlhZ2U="}]
FILES_WITH_TYPE = [{**FILES[0], "content_type": "text/markdown"}]


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
    skill_registry_context.agents.get_skill.assert_awaited_once_with(
        "triage", skill_uuid=None
    )
    skill_registry_context.agents.get_skill_draft.assert_awaited_once_with(
        "triage", skill_uuid=None
    )


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
        skill_id="triage", path="SKILL.md", skill_uuid=None
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
        skill_id="triage", base_revision=1, operations=operations, skill_uuid=None
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
        skill_id="triage", base_revision=5, operations=operations, skill_uuid=None
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
        skill_id="triage", skill_uuid=None, limit=10, cursor="cursor-1", reverse=True
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
        skill_id="triage", skill_uuid=None, version_id=version_id
    )


@pytest.mark.anyio
async def test_update_skill_without_inputs_republishes_current_version(
    skill_registry_context: MagicMock,
) -> None:
    agents = skill_registry_context.agents
    agents.get_skill = AsyncMock(return_value={"current_version_id": "v1"})
    agents.get_skill_version = AsyncMock(return_value={"files": FILES_WITH_TYPE})
    agents.publish_skill_version = AsyncMock(return_value={"id": "v2"})

    assert await update_skill("triage") == {"id": "v2"}
    agents.publish_skill_version.assert_awaited_once_with(
        skill_id="triage", skill_uuid=None, base_version_id="v1", files=FILES_WITH_TYPE
    )


@pytest.mark.anyio
async def test_update_skill_rejects_operations_with_legacy_files(
    skill_registry_context: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="not both"):
        await update_skill(
            "triage",
            operations=[{"op": "delete_file", "path": "a.md"}],
            files=FILES,
        )


@pytest.mark.anyio
async def test_update_skill_legacy_files_merge_and_publish(
    skill_registry_context: MagicMock,
) -> None:
    agents = skill_registry_context.agents
    agents.get_skill = AsyncMock(return_value={"current_version_id": "v1"})
    agents.get_skill_version = AsyncMock(
        return_value={
            "files": [
                {"path": p, "content_base64": "b2xk", "content_type": "text/plain"}
                for p in ("SKILL.md", "old.md")
            ]
        }
    )
    agents.publish_skill_version = AsyncMock(return_value={"id": "v2"})

    result = await update_skill(
        "triage", files=FILES, delete_paths=["old.md"], skill_uuid=SKILL_UUID
    )

    assert result == {"id": "v2"}
    agents.get_skill.assert_awaited_once_with("triage", skill_uuid=SKILL_UUID)
    agents.publish_skill_version.assert_awaited_once_with(
        skill_id="triage", skill_uuid=SKILL_UUID, base_version_id="v1", files=FILES
    )


# Frozen 1.2.1 inputs: saved workflows may pass any of these.
STABLE_1_2_1_INPUTS: dict[str, set[str]] = {
    "list_skills": {"limit", "cursor", "reverse"},
    "create_skill": {"name", "description"},
    "get_skill": {"skill_id", "skill_uuid"},
    "list_skill_versions": {"skill_id", "skill_uuid", "limit", "cursor", "reverse"},
    "get_skill_version": {"skill_id", "version_id", "skill_uuid"},
    "publish_skill_version": {"skill_id", "files", "base_version_id", "skill_uuid"},
    "restore_skill_version": {"skill_id", "version_id", "skill_uuid"},
    "archive_skill": {"skill_id", "skill_uuid"},
    "get_skill_draft": {"skill_id", "skill_uuid"},
    "get_skill_draft_file": {"skill_id", "path", "skill_uuid"},
    "update_skill_draft": {"skill_id", "base_revision", "operations", "skill_uuid"},
    "publish_skill_draft": {"skill_id", "skill_uuid"},
    "update_skill": {"skill_id", "files", "delete_paths", "skill_uuid"},
}
DEPRECATED_ACTIONS = (
    "archive_skill",
    "get_skill_draft",
    "get_skill_draft_file",
    "update_skill_draft",
    "publish_skill_draft",
)


@pytest.mark.parametrize(("name", "inputs"), STABLE_1_2_1_INPUTS.items())
def test_stable_skill_action_inputs_are_still_accepted(
    name: str, inputs: set[str]
) -> None:
    schema = input_schema_for(core_udfs()[f"ai.skill.{name}"])
    assert inputs <= schema["properties"].keys()
    assert set(schema.get("required", [])) <= inputs


def test_update_skill_schema_marks_legacy_fields_deprecated() -> None:
    properties = input_schema_for(update_skill)["properties"]
    for field in ("files", "delete_paths", "skill_uuid"):
        assert properties[field]["deprecated"] is True


@pytest.mark.parametrize("name", DEPRECATED_ACTIONS)
def test_legacy_skill_actions_are_deprecated(name: str) -> None:
    udf = core_udfs()[f"ai.skill.{name}"]
    assert getattr(udf, "__tracecat_udf_kwargs")["deprecated"]
