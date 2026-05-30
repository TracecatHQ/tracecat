"""Core registry UDFs for managing workspace agent skills."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.context import get_context
from tracecat_registry.sdk.agents import CursorPage


@registry.register(
    default_title="List agent skills",
    display_group="Agent Skills",
    description="List workspace agent skills with cursor pagination.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def list_skills(
    limit: Annotated[int, Doc("Page size.")] = 20,
    cursor: Annotated[
        str | None, Doc("Optional cursor from a previous response.")
    ] = None,
    reverse: Annotated[bool, Doc("Whether to reverse sort order.")] = False,
) -> CursorPage:
    return await get_context().agents.list_skills(
        limit=limit, cursor=cursor, reverse=reverse
    )


@registry.register(
    default_title="Create agent skill",
    display_group="Agent Skills",
    description="Create a workspace agent skill shell before publishing a version.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def create_skill(
    name: Annotated[str, Doc("Skill name in kebab-case (e.g., 'triage-assistant').")],
    description: Annotated[str | None, Doc("Optional skill description.")] = None,
) -> dict[str, Any]:
    return await get_context().agents.create_skill(name=name, description=description)


@registry.register(
    default_title="Get agent skill",
    display_group="Agent Skills",
    description="Get one workspace agent skill by skill ID, with an optional UUID override.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def get_skill(
    skill_id: Annotated[str, Doc("Skill ID/name in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await get_context().agents.get_skill(skill_id, skill_uuid=skill_uuid)


@registry.register(
    default_title="List agent skill versions",
    display_group="Agent Skills",
    description="List immutable published versions for a workspace agent skill.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def list_skill_versions(
    skill_id: Annotated[str, Doc("Skill ID/name in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
    limit: Annotated[int, Doc("Page size.")] = 20,
    cursor: Annotated[
        str | None, Doc("Optional cursor from a previous response.")
    ] = None,
    reverse: Annotated[bool, Doc("Whether to reverse sort order.")] = False,
) -> CursorPage:
    return await get_context().agents.list_skill_versions(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )


@registry.register(
    default_title="Get agent skill version",
    display_group="Agent Skills",
    description="Get one immutable published skill version by ID, including publish-compatible file contents.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def get_skill_version(
    skill_id: Annotated[str, Doc("Skill ID/name in kebab-case.")],
    version_id: Annotated[uuid.UUID, Doc("Skill version UUID.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await get_context().agents.get_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        version_id=version_id,
    )


@registry.register(
    default_title="Publish agent skill version",
    display_group="Agent Skills",
    description="Publish a complete file set as a new immutable skill version.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def publish_skill_version(
    skill_id: Annotated[str, Doc("Skill ID/name in kebab-case.")],
    files: Annotated[
        list[dict[str, Any]],
        Doc(
            "Complete version file set. Each file requires path and content_base64, with optional content_type."
        ),
    ],
    base_version_id: Annotated[
        str | None,
        Doc(
            "Current version UUID observed before publishing. Omit or use null for the first version."
        ),
    ] = None,
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await get_context().agents.publish_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        base_version_id=base_version_id,
        files=files,
    )


@registry.register(
    default_title="Restore agent skill version",
    display_group="Agent Skills",
    description="Restore a historical published version as the current skill version.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def restore_skill_version(
    skill_id: Annotated[str, Doc("Skill ID/name in kebab-case.")],
    version_id: Annotated[uuid.UUID, Doc("Skill version UUID.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await get_context().agents.restore_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        version_id=version_id,
    )


@registry.register(
    default_title="Archive agent skill",
    display_group="Agent Skills",
    description="Archive (delete) a workspace skill.",
    namespace="ai.skill",
    required_entitlements=["agent_addons"],
)
async def archive_skill(
    skill_id: Annotated[str, Doc("Skill ID/name in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> None:
    await get_context().agents.archive_skill(skill_id, skill_uuid=skill_uuid)
