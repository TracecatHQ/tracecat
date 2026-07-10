from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.presets import (
    add_preset_skill,
    add_preset_subagent,
    get_preset_subagent,
    list_preset_skills,
    list_preset_subagents,
    remove_preset_skill,
    remove_preset_subagent,
    update_preset_skill,
    update_preset_subagent,
)


@pytest.fixture
async def preset_registry_context() -> AsyncIterator[MagicMock]:
    ctx = MagicMock()
    ctx.agents = MagicMock()
    set_context(cast(RegistryContext, ctx))
    try:
        yield ctx
    finally:
        clear_context()


@pytest.mark.anyio
async def test_list_preset_skills_returns_skill_bindings(
    preset_registry_context: MagicMock,
) -> None:
    skill_id = uuid.uuid4()
    version_id = uuid.uuid4()
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={
            "skills": [{"skill_id": str(skill_id), "skill_version_id": str(version_id)}]
        }
    )

    result = await list_preset_skills("triage")

    assert result == [{"skill_id": str(skill_id), "skill_version_id": str(version_id)}]
    preset_registry_context.agents.get_preset.assert_awaited_once_with("triage")


@pytest.mark.anyio
async def test_add_preset_skill_appends_versioned_binding(
    preset_registry_context: MagicMock,
) -> None:
    existing_skill_id = uuid.uuid4()
    existing_version_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    version_id = uuid.uuid4()
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={
            "skills": [
                {
                    "skill_id": str(existing_skill_id),
                    "skill_version_id": str(existing_version_id),
                }
            ]
        }
    )
    preset_registry_context.agents.update_preset = AsyncMock(
        return_value={"slug": "triage"}
    )

    result = await add_preset_skill("triage", skill_id, version_id)

    assert result == {"slug": "triage"}
    preset_registry_context.agents.update_preset.assert_awaited_once_with(
        "triage",
        skills=[
            {
                "skill_id": str(existing_skill_id),
                "skill_version_id": str(existing_version_id),
            },
            {"skill_id": str(skill_id), "skill_version_id": str(version_id)},
        ],
    )


@pytest.mark.anyio
async def test_update_preset_skill_replaces_versioned_binding(
    preset_registry_context: MagicMock,
) -> None:
    skill_id = uuid.uuid4()
    old_version_id = uuid.uuid4()
    new_version_id = uuid.uuid4()
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={
            "skills": [
                {"skill_id": str(skill_id), "skill_version_id": str(old_version_id)}
            ]
        }
    )
    preset_registry_context.agents.update_preset = AsyncMock(
        return_value={"slug": "triage"}
    )

    result = await update_preset_skill("triage", skill_id, new_version_id)

    assert result == {"slug": "triage"}
    preset_registry_context.agents.update_preset.assert_awaited_once_with(
        "triage",
        skills=[{"skill_id": str(skill_id), "skill_version_id": str(new_version_id)}],
    )


@pytest.mark.anyio
async def test_remove_preset_skill_deletes_binding(
    preset_registry_context: MagicMock,
) -> None:
    skill_id = uuid.uuid4()
    version_id = uuid.uuid4()
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={
            "skills": [{"skill_id": str(skill_id), "skill_version_id": str(version_id)}]
        }
    )
    preset_registry_context.agents.update_preset = AsyncMock(
        return_value={"slug": "triage"}
    )

    result = await remove_preset_skill("triage", skill_id)

    assert result == {"slug": "triage"}
    preset_registry_context.agents.update_preset.assert_awaited_once_with(
        "triage",
        skills=[],
    )


@pytest.mark.anyio
async def test_list_and_get_preset_subagents(
    preset_registry_context: MagicMock,
) -> None:
    subagent = {"preset": "researcher", "preset_version": 3, "name": "lookup"}
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={"agents": {"enabled": True, "subagents": [subagent]}}
    )

    assert await list_preset_subagents("triage") == [subagent]
    assert await get_preset_subagent("triage", "lookup") == subagent


@pytest.mark.anyio
async def test_add_preset_subagent_appends_versioned_binding(
    preset_registry_context: MagicMock,
) -> None:
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={"agents": {"enabled": False, "subagents": []}}
    )
    preset_registry_context.agents.update_preset = AsyncMock(
        return_value={"slug": "triage"}
    )

    result = await add_preset_subagent(
        "triage",
        "researcher",
        preset_version=3,
        name="lookup",
        description="Research suspicious artifacts",
        max_turns=5,
    )

    assert result == {"slug": "triage"}
    preset_registry_context.agents.update_preset.assert_awaited_once_with(
        "triage",
        agents={
            "enabled": True,
            "subagents": [
                {
                    "preset": "researcher",
                    "preset_version": 3,
                    "name": "lookup",
                    "description": "Research suspicious artifacts",
                    "max_turns": 5,
                }
            ],
        },
    )


@pytest.mark.anyio
async def test_update_preset_subagent_updates_version_pin(
    preset_registry_context: MagicMock,
) -> None:
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={
            "agents": {
                "enabled": True,
                "subagents": [
                    {"preset": "researcher", "preset_version": 1, "name": "lookup"}
                ],
            }
        }
    )
    preset_registry_context.agents.update_preset = AsyncMock(
        return_value={"slug": "triage"}
    )

    result = await update_preset_subagent("triage", "lookup", preset_version=2)

    assert result == {"slug": "triage"}
    preset_registry_context.agents.update_preset.assert_awaited_once_with(
        "triage",
        agents={
            "enabled": True,
            "subagents": [
                {"preset": "researcher", "preset_version": 2, "name": "lookup"}
            ],
        },
    )


@pytest.mark.anyio
async def test_remove_preset_subagent_removes_binding_and_disables_when_empty(
    preset_registry_context: MagicMock,
) -> None:
    preset_registry_context.agents.get_preset = AsyncMock(
        return_value={
            "agents": {
                "enabled": True,
                "subagents": [
                    {"preset": "researcher", "preset_version": 2, "name": "lookup"}
                ],
            }
        }
    )
    preset_registry_context.agents.update_preset = AsyncMock(
        return_value={"slug": "triage"}
    )

    result = await remove_preset_subagent("triage", "lookup")

    assert result == {"slug": "triage"}
    preset_registry_context.agents.update_preset.assert_awaited_once_with(
        "triage",
        agents={"enabled": False, "subagents": []},
    )
