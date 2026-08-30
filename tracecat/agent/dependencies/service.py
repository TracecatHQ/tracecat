"""Coordination for mutations spanning Agent presets and Skills."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import select

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.db.models import AgentPreset, AgentPresetSkill, Skill
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService


class AgentDependencyService(BaseWorkspaceService):
    """Coordinate cross-resource unlinking and head publication."""

    service_name = "agent_dependency"

    async def unlink_skill_from_active_presets(
        self,
        skill_id: uuid.UUID,
        *,
        confirm_unlink: bool,
    ) -> Skill:
        """Lock a Skill graph, unlink active parents, and return the target row."""

        locked_skills = {
            skill.id: skill
            for skill in (
                await self.session.execute(
                    select(Skill)
                    .where(
                        Skill.workspace_id == self.workspace_id,
                        Skill.deleted_at.is_(None),
                        Skill.archived_at.is_(None),
                    )
                    .order_by(Skill.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        }
        skill = locked_skills.get(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")

        stmt = (
            select(AgentPreset)
            .join(AgentPresetSkill, AgentPresetSkill.preset_id == AgentPreset.id)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.deleted_at.is_(None),
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.skill_id == skill_id,
            )
            .order_by(AgentPreset.id)
            .with_for_update()
        )
        parents = list((await self.session.execute(stmt)).scalars().unique().all())
        if parents and not confirm_unlink:
            raise TracecatValidationError(
                "Deleting this skill requires confirmation because it is still "
                "referenced by agent presets",
                detail={
                    "code": "skill_in_use",
                    "head_reference_count": len(parents),
                },
            )

        preset_service = AgentPresetService(self.session, role=self.role)
        for parent in parents:
            bound_skill_ids = list(
                (
                    await self.session.execute(
                        select(AgentPresetSkill.skill_id).where(
                            AgentPresetSkill.workspace_id == self.workspace_id,
                            AgentPresetSkill.preset_id == parent.id,
                            AgentPresetSkill.skill_id != skill_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            binding_specs: list[SkillBindingSpec] = []
            for bound_skill_id in sorted(bound_skill_ids, key=str):
                bound_skill = locked_skills.get(bound_skill_id)
                if bound_skill is None:
                    raise TracecatValidationError(
                        "Some preset skills are archived and cannot be published",
                        detail={
                            "code": "skill_not_found",
                            "skill_id": str(bound_skill_id),
                            "preset_id": str(parent.id),
                        },
                    )
                if bound_skill.current_version_id is None:
                    raise TracecatValidationError(
                        f"Skill '{bound_skill.name}' has no published version",
                        detail={
                            "code": "skill_not_published",
                            "skill_id": str(bound_skill.id),
                        },
                    )
                binding_specs.append(
                    SkillBindingSpec(
                        bound_skill.id,
                        bound_skill.current_version_id,
                    )
                )
            await self.session.execute(
                sa.delete(AgentPresetSkill).where(
                    AgentPresetSkill.workspace_id == self.workspace_id,
                    AgentPresetSkill.preset_id == parent.id,
                    AgentPresetSkill.skill_id == skill_id,
                )
            )
            await preset_service.publish_preset_head(
                parent,
                preset_locked=True,
                binding_specs=binding_specs,
            )
        return skill
