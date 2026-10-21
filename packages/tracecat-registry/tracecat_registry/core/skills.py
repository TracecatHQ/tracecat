"""Core registry UDFs for managing workspace agent skills.

Skills are edited as a mutable draft and published into immutable versions.
Agents always run the current published version.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import ctx, registry
from tracecat_registry.sdk.agents import CursorPage

SkillId = Annotated[str, Doc("Skill slug (e.g., 'triage-assistant') or skill ID.")]


@registry.register(
    default_title="List skills",
    display_group="Agent Skills",
    description="List workspace skills with their slugs and current published versions.",
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
    default_title="Get skill",
    display_group="Agent Skills",
    description=(
        "Get a skill and its draft file manifest, or read one draft file's contents by path."
    ),
    namespace="ai.skill",
)
async def get_skill(
    skill_id: SkillId,
    path: Annotated[
        str | None,
        Doc(
            "Optional file path to read (e.g., 'SKILL.md'). Text files are returned "
            "in text_content; other files return a short-lived download_url. "
            "Omit to return the skill with its draft_files manifest."
        ),
    ] = None,
) -> dict[str, Any]:
    if path is not None:
        return await ctx.agents.aio.get_skill_draft_file(skill_id=skill_id, path=path)
    skill = await ctx.agents.aio.get_skill(skill_id)
    draft = await ctx.agents.aio.get_skill_draft(skill_id)
    return {**skill, "draft_files": draft.get("files", [])}


@registry.register(
    default_title="Create skill",
    display_group="Agent Skills",
    description="Create an empty workspace skill. Add files with update_skill, then publish it.",
    namespace="ai.skill",
)
async def create_skill(
    name: Annotated[str, Doc("Skill name in kebab-case (e.g., 'triage-assistant').")],
    description: Annotated[str | None, Doc("Optional skill description.")] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.create_skill(name=name, description=description)


@registry.register(
    default_title="Update skill",
    display_group="Agent Skills",
    description="Add, replace, move, or delete files in a skill's draft. Call publish_skill to make the changes live.",
    namespace="ai.skill",
)
async def update_skill(
    skill_id: SkillId,
    operations: Annotated[
        list[dict[str, Any]],
        Doc(
            "File operations applied in order. Each has an 'op' of "
            "'upsert_text_file' (path, content, optional content_type), "
            "'delete_file' (path), or 'move_file' (from_path, to_path)."
        ),
    ],
    base_revision: Annotated[
        int | None,
        Doc(
            "Draft revision from get_skill. If set, the update is rejected when the "
            "draft changed since. Omit to apply on top of the latest draft."
        ),
    ] = None,
) -> dict[str, Any]:
    revision = base_revision
    if revision is None:
        draft = await ctx.agents.aio.get_skill_draft(skill_id)
        revision = draft["draft_revision"]
    return await ctx.agents.aio.patch_skill_draft(
        skill_id=skill_id,
        base_revision=revision,
        operations=operations,
    )


@registry.register(
    default_title="Publish skill",
    display_group="Agent Skills",
    description="Publish a skill's draft as a new immutable version. Agents using the skill pick it up on their next run.",
    namespace="ai.skill",
)
async def publish_skill(skill_id: SkillId) -> dict[str, Any]:
    return await ctx.agents.aio.publish_skill_draft(skill_id)


@registry.register(
    default_title="Delete skill",
    display_group="Agent Skills",
    description="Delete a workspace skill.",
    namespace="ai.skill",
)
async def delete_skill(skill_id: SkillId) -> None:
    await ctx.agents.aio.archive_skill(skill_id)


@registry.register(
    default_title="List skill versions",
    display_group="Agent Skills",
    description="List a skill's published versions, e.g. to find one to restore.",
    namespace="ai.skill",
)
async def list_skill_versions(
    skill_id: SkillId,
    limit: Annotated[int, Doc("Page size.")] = 20,
    cursor: Annotated[
        str | None, Doc("Optional cursor from a previous response.")
    ] = None,
    reverse: Annotated[bool, Doc("Whether to reverse sort order.")] = False,
) -> CursorPage:
    return await ctx.agents.aio.list_skill_versions(
        skill_id=skill_id,
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )


@registry.register(
    default_title="Restore skill version",
    display_group="Agent Skills",
    description="Roll a skill back to a previously published version.",
    namespace="ai.skill",
)
async def restore_skill_version(
    skill_id: SkillId,
    version_id: Annotated[uuid.UUID, Doc("Version ID from list_skill_versions.")],
) -> dict[str, Any]:
    return await ctx.agents.aio.restore_skill_version(
        skill_id=skill_id,
        version_id=version_id,
    )
