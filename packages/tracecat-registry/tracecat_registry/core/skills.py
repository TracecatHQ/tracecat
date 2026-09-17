"""Core registry UDFs for managing workspace agent skills."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import ctx, registry
from tracecat_registry.sdk.agents import CursorPage


@registry.register(
    default_title="List agent skills",
    display_group="Agent Skills",
    description="List workspace agent skills with cursor pagination.",
    namespace="ai.skill",
)
async def list_skills(
    limit: Annotated[int, Doc("Page size.")] = 20,
    cursor: Annotated[
        str | None, Doc("Optional cursor from a previous response.")
    ] = None,
    reverse: Annotated[bool, Doc("Whether to reverse sort order.")] = False,
) -> CursorPage:
    return await ctx.agents.aio.list_skills(limit=limit, cursor=cursor, reverse=reverse)


@registry.register(
    default_title="Create agent skill",
    display_group="Agent Skills",
    description="Create a workspace agent skill shell before publishing a version.",
    namespace="ai.skill",
)
async def create_skill(
    name: Annotated[str, Doc("Skill name in kebab-case (e.g., 'triage-assistant').")],
    description: Annotated[str | None, Doc("Optional skill description.")] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.create_skill(name=name, description=description)


@registry.register(
    default_title="Get agent skill",
    display_group="Agent Skills",
    description="Get one workspace agent skill by slug, with an optional UUID override.",
    namespace="ai.skill",
)
async def get_skill(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.get_skill(skill_id, skill_uuid=skill_uuid)


@registry.register(
    default_title="List agent skill versions",
    display_group="Agent Skills",
    description="List immutable published versions for a workspace agent skill.",
    namespace="ai.skill",
)
async def list_skill_versions(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
    limit: Annotated[int, Doc("Page size.")] = 20,
    cursor: Annotated[
        str | None, Doc("Optional cursor from a previous response.")
    ] = None,
    reverse: Annotated[bool, Doc("Whether to reverse sort order.")] = False,
) -> CursorPage:
    return await ctx.agents.aio.list_skill_versions(
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
)
async def get_skill_version(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    version_id: Annotated[uuid.UUID, Doc("Skill version UUID.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.get_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        version_id=version_id,
    )


@registry.register(
    default_title="Publish agent skill version",
    display_group="Agent Skills",
    description="Publish a complete file set as a new immutable skill version.",
    namespace="ai.skill",
)
async def publish_skill_version(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
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
    return await ctx.agents.aio.publish_skill_version(
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
)
async def restore_skill_version(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    version_id: Annotated[uuid.UUID, Doc("Skill version UUID.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.restore_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        version_id=version_id,
    )


@registry.register(
    default_title="Archive agent skill",
    display_group="Agent Skills",
    description="Archive (delete) a workspace skill.",
    namespace="ai.skill",
)
async def archive_skill(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> None:
    await ctx.agents.aio.archive_skill(skill_id, skill_uuid=skill_uuid)


@registry.register(
    default_title="Get agent skill draft",
    display_group="Agent Skills",
    description="Get the current mutable draft state for a workspace agent skill, including its file manifest and validation errors.",
    namespace="ai.skill",
)
async def get_skill_draft(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.get_skill_draft(skill_id, skill_uuid=skill_uuid)


@registry.register(
    default_title="Get agent skill draft file",
    display_group="Agent Skills",
    description="Get one file from the current mutable draft for a workspace agent skill.",
    namespace="ai.skill",
)
async def get_skill_draft_file(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    path: Annotated[str, Doc("Draft file path (e.g., 'SKILL.md').")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.get_skill_draft_file(
        skill_id=skill_id,
        path=path,
        skill_uuid=skill_uuid,
    )


@registry.register(
    default_title="Update agent skill draft",
    display_group="Agent Skills",
    description="Apply file operations to a workspace agent skill draft without publishing.",
    namespace="ai.skill",
)
async def update_skill_draft(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    base_revision: Annotated[
        int,
        Doc(
            "Draft revision observed before editing (from get_skill or get_skill_draft). Rejected with a conflict if the draft changed."
        ),
    ],
    operations: Annotated[
        list[dict[str, Any]],
        Doc(
            "Draft operations applied in order. Each has an 'op' of 'upsert_text_file' (path, content, optional content_type), 'delete_file' (path), or 'move_file' (from_path, to_path)."
        ),
    ],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.patch_skill_draft(
        skill_id=skill_id,
        base_revision=base_revision,
        operations=operations,
        skill_uuid=skill_uuid,
    )


@registry.register(
    default_title="Publish agent skill draft",
    display_group="Agent Skills",
    description="Publish the current draft as a new immutable skill version.",
    namespace="ai.skill",
)
async def publish_skill_draft(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.publish_skill_draft(skill_id, skill_uuid=skill_uuid)


@registry.register(
    default_title="Update agent skill",
    display_group="Agent Skills",
    description="Publish a new skill version by applying file upserts and deletions on top of the current published version.",
    namespace="ai.skill",
)
async def update_skill(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    files: Annotated[
        list[dict[str, Any]] | None,
        Doc(
            "Files to add or replace. Each file requires path and content_base64, with optional content_type."
        ),
    ] = None,
    delete_paths: Annotated[
        list[str] | None, Doc("File paths to remove from the current version.")
    ] = None,
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    skill = await ctx.agents.aio.get_skill(skill_id, skill_uuid=skill_uuid)
    current_version_id = skill.get("current_version_id")
    merged: dict[str, dict[str, Any]] = {}
    if current_version_id is not None:
        snapshot = await ctx.agents.aio.get_skill_version(
            skill_id=skill_id,
            skill_uuid=skill_uuid,
            version_id=current_version_id,
        )
        for file in snapshot.get("files", []):
            merged[file["path"]] = {
                "path": file["path"],
                "content_base64": file["content_base64"],
                "content_type": file["content_type"],
            }
    for path in delete_paths or ():
        merged.pop(path, None)
    for file in files or ():
        merged[file["path"]] = file
    if not merged:
        raise ValueError("Skill update must leave at least one file")
    return await ctx.agents.aio.publish_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        base_version_id=current_version_id,
        files=list(merged.values()),
    )
