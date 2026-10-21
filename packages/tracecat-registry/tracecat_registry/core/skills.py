"""Core registry UDFs for managing workspace agent skills.

Skills are edited as a mutable draft and published into immutable versions.
Agents always run the current published version.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import ctx, registry
from tracecat_registry.sdk.agents import CursorPage

SkillId = Annotated[str, Doc("Skill slug (e.g., 'triage-assistant') or skill ID.")]

LEGACY_UPDATE_FIELD_SCHEMA_EXTRA: dict[str, Any] = {
    "x-tracecat-deprecation-message": "Use `operations` instead."
}
LEGACY_SKILL_UUID_SCHEMA_EXTRA: dict[str, Any] = {
    "x-tracecat-deprecation-message": "Use `skill_id` instead."
}
LegacySkillUuid = Annotated[
    uuid.UUID | None,
    Doc("Deprecated canonical skill UUID. Use skill_id instead."),
    Field(deprecated=True, json_schema_extra=LEGACY_SKILL_UUID_SCHEMA_EXTRA),
]


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
    skill_uuid: LegacySkillUuid = None,
) -> dict[str, Any]:
    if path is not None:
        return await ctx.agents.aio.get_skill_draft_file(
            skill_id=skill_id, path=path, skill_uuid=skill_uuid
        )
    skill = await ctx.agents.aio.get_skill(skill_id, skill_uuid=skill_uuid)
    draft = await ctx.agents.aio.get_skill_draft(skill_id, skill_uuid=skill_uuid)
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
        list[dict[str, Any]] | None,
        Doc(
            "File operations applied in order. Each has an 'op' of "
            "'upsert_text_file' (path, content, optional content_type), "
            "'delete_file' (path), or 'move_file' (from_path, to_path). "
            "Required unless using the deprecated files/delete_paths."
        ),
    ] = None,
    base_revision: Annotated[
        int | None,
        Doc(
            "Draft revision from get_skill. If set, the update is rejected when the "
            "draft changed since. Omit to apply on top of the latest draft."
        ),
    ] = None,
    files: Annotated[
        list[dict[str, Any]] | None,
        Doc(
            "Deprecated files to add or replace, published immediately. Use operations instead."
        ),
        Field(deprecated=True, json_schema_extra=LEGACY_UPDATE_FIELD_SCHEMA_EXTRA),
    ] = None,
    delete_paths: Annotated[
        list[str] | None,
        Doc(
            "Deprecated file paths to remove, published immediately. Use operations instead."
        ),
        Field(deprecated=True, json_schema_extra=LEGACY_UPDATE_FIELD_SCHEMA_EXTRA),
    ] = None,
    skill_uuid: LegacySkillUuid = None,
) -> dict[str, Any]:
    if files is not None or delete_paths is not None:
        if operations is not None:
            raise ValueError("Pass either operations or files/delete_paths, not both")
        return await _publish_merged_files(
            skill_id=skill_id,
            files=files,
            delete_paths=delete_paths,
            skill_uuid=skill_uuid,
        )
    if operations is None:
        raise ValueError("operations is required")
    revision = base_revision
    if revision is None:
        draft = await ctx.agents.aio.get_skill_draft(skill_id, skill_uuid=skill_uuid)
        revision = draft["draft_revision"]
    return await ctx.agents.aio.patch_skill_draft(
        skill_id=skill_id,
        base_revision=revision,
        operations=operations,
        skill_uuid=skill_uuid,
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
    skill_uuid: LegacySkillUuid = None,
) -> CursorPage:
    return await ctx.agents.aio.list_skill_versions(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
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
    skill_uuid: LegacySkillUuid = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.restore_skill_version(
        skill_id=skill_id,
        skill_uuid=skill_uuid,
        version_id=version_id,
    )


async def _publish_merged_files(
    *,
    skill_id: str,
    files: list[dict[str, Any]] | None,
    delete_paths: list[str] | None,
    skill_uuid: uuid.UUID | None,
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


# Deprecated 1.2.1 actions kept so saved workflows keep running.


@registry.register(
    default_title="Get agent skill version",
    display_group="Agent Skills",
    description="Get one immutable published skill version by ID, including publish-compatible file contents.",
    namespace="ai.skill",
    deprecated="Deprecated with no replacement.",
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
    deprecated="Use ai.skill.update_skill, then ai.skill.publish_skill instead.",
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
    default_title="Archive agent skill",
    display_group="Agent Skills",
    description="Archive (delete) a workspace skill.",
    namespace="ai.skill",
    deprecated="Use ai.skill.delete_skill instead.",
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
    deprecated="Use ai.skill.get_skill instead.",
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
    deprecated="Use ai.skill.get_skill with path instead.",
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
    deprecated="Use ai.skill.update_skill instead.",
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
    deprecated="Use ai.skill.publish_skill instead.",
)
async def publish_skill_draft(
    skill_id: Annotated[str, Doc("Skill slug in kebab-case.")],
    skill_uuid: Annotated[
        uuid.UUID | None, Doc("Optional canonical skill UUID.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.agents.aio.publish_skill_draft(skill_id, skill_uuid=skill_uuid)
