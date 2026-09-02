"""Skill binding validation and preset-version resolution."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Never

import sqlalchemy as sa
from sqlalchemy import select

from tracecat.agent.preset.schemas import AgentPresetSkillBindingBase
from tracecat.agent.skill.types import ResolvedSkillRef
from tracecat.db.models import AgentPresetVersionSkill, Skill, SkillVersion
from tracecat.db.soft_delete import with_deleted
from tracecat.exceptions import TracecatValidationError
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


class SkillBindingService(BaseWorkspaceService):
    """Validate mutable Skill bindings and resolve preset-version references."""

    service_name = "skill_binding"

    async def _get_bindable_skills(
        self,
        skill_ids: Sequence[uuid.UUID],
        *,
        for_update: bool = False,
    ) -> dict[uuid.UUID, Skill]:
        """Return active Skills, optionally locked in deterministic UUID order."""

        normalized_ids = sorted(set(skill_ids), key=str)
        if not normalized_ids:
            return {}

        if not for_update:
            stmt = select(Skill).where(
                Skill.workspace_id == self.workspace_id,
                Skill.id.in_(normalized_ids),
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            return {
                skill.id: skill
                for skill in (await self.session.execute(stmt)).scalars().all()
            }

        stmt = (
            select(Skill)
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.id.in_(normalized_ids),
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .order_by(Skill.id)
            .with_for_update()
        )
        return {
            skill.id: skill
            for skill in (await self.session.execute(stmt)).scalars().all()
        }

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def validate_binding_inputs(
        self,
        bindings: Sequence[AgentPresetSkillBindingBase],
        *,
        for_update: bool = False,
    ) -> None:
        """Validate preset Skill bindings before they are persisted."""

        if not bindings:
            return
        if len({binding.skill_id for binding in bindings}) != len(bindings):
            raise TracecatValidationError(
                "Duplicate skills are not allowed on a preset",
                detail={"code": "duplicate_skill_binding"},
            )

        skill_ids = [binding.skill_id for binding in bindings]
        skills = await self._get_bindable_skills(skill_ids, for_update=for_update)
        missing = [str(skill_id) for skill_id in skill_ids if skill_id not in skills]
        if missing:
            raise TracecatValidationError(
                f"Some skills were not found in this workspace: {sorted(missing)}",
                detail={"code": "skill_not_found", "missing_skill_ids": missing},
            )

        for binding in bindings:
            skill = skills[binding.skill_id]
            if skill.current_version_id is None:
                raise TracecatValidationError(
                    f"Skill '{skill.name}' has no published version",
                    detail={"code": "skill_not_published", "skill_id": str(skill.id)},
                )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_resolved_skill_refs_for_preset_version(
        self,
        preset_version_id: uuid.UUID,
        *,
        use_latest_versions: bool = False,
    ) -> list[ResolvedSkillRef]:
        """Return Skill refs for an immutable preset version."""

        if use_latest_versions:
            return await self._get_latest_skill_refs_for_preset_version(
                preset_version_id
            )

        stmt = (
            select(
                AgentPresetVersionSkill.skill_id,
                SkillVersion.name,
                AgentPresetVersionSkill.skill_version_id,
                SkillVersion.manifest_sha256,
                Skill.deleted_at,
                Skill.archived_at,
            )
            .join(
                SkillVersion,
                sa.and_(
                    AgentPresetVersionSkill.workspace_id == SkillVersion.workspace_id,
                    AgentPresetVersionSkill.skill_id == SkillVersion.skill_id,
                    AgentPresetVersionSkill.skill_version_id == SkillVersion.id,
                ),
            )
            .join(
                Skill,
                sa.and_(
                    AgentPresetVersionSkill.workspace_id == Skill.workspace_id,
                    AgentPresetVersionSkill.skill_id == Skill.id,
                ),
            )
            .where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == preset_version_id,
            )
            .order_by(SkillVersion.name.asc(), AgentPresetVersionSkill.skill_id.asc())
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        resolved: list[ResolvedSkillRef] = []
        for (
            skill_id,
            skill_name,
            skill_version_id,
            manifest_sha256,
            _deleted_at,
            _archived_at,
        ) in rows:
            if skill_name is None:
                continue
            resolved.append(
                ResolvedSkillRef(
                    skill_id=skill_id,
                    skill_name=skill_name,
                    skill_version_id=skill_version_id,
                    manifest_sha256=manifest_sha256,
                )
            )
        return resolved

    async def _get_latest_skill_refs_for_preset_version(
        self, preset_version_id: uuid.UUID
    ) -> list[ResolvedSkillRef]:
        """Return current Skill versions for a preset version's Skill IDs."""

        stmt = (
            select(
                AgentPresetVersionSkill.skill_id,
                Skill.name,
                Skill.current_version_id,
                Skill.deleted_at,
                Skill.archived_at,
                SkillVersion.name,
                SkillVersion.manifest_sha256,
            )
            .join(
                Skill,
                sa.and_(
                    AgentPresetVersionSkill.workspace_id == Skill.workspace_id,
                    AgentPresetVersionSkill.skill_id == Skill.id,
                ),
            )
            .outerjoin(
                SkillVersion,
                sa.and_(
                    SkillVersion.workspace_id == Skill.workspace_id,
                    SkillVersion.skill_id == Skill.id,
                    SkillVersion.id == Skill.current_version_id,
                ),
            )
            .where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == preset_version_id,
            )
            .order_by(
                SkillVersion.name.asc().nulls_last(),
                Skill.name.asc(),
                AgentPresetVersionSkill.skill_id.asc(),
            )
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        resolved: list[ResolvedSkillRef] = []
        missing_current: list[str] = []
        for (
            skill_id,
            skill_name,
            current_version_id,
            _deleted_at,
            _archived_at,
            current_version_name,
            manifest_sha256,
        ) in rows:
            if current_version_id is None:
                missing_current.append(f"{skill_name} ({skill_id})")
                continue
            if current_version_name is None:
                self._raise_missing_version_name(skill_version_id=current_version_id)
            resolved.append(
                ResolvedSkillRef(
                    skill_id=skill_id,
                    skill_name=current_version_name,
                    skill_version_id=current_version_id,
                    manifest_sha256=manifest_sha256,
                )
            )

        if missing_current:
            raise TracecatValidationError(
                "Some skills have no current published version",
                detail={
                    "code": "skill_not_published",
                    "skills": sorted(missing_current),
                    "preset_version_id": str(preset_version_id),
                },
            )
        return resolved

    @staticmethod
    def _raise_missing_version_name(*, skill_version_id: uuid.UUID) -> Never:
        """Raise when a published Skill version is missing its required name."""

        raise TracecatValidationError(
            f"Skill version '{skill_version_id}' is missing a required name",
            detail={
                "code": "missing_skill_version_name",
                "skill_version_id": str(skill_version_id),
            },
        )
