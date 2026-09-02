"""Relational storage for preset-backed subagent head edges."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select

from tracecat.agent.subagents import (
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.db.models import (
    AgentPreset,
    AgentPresetSubagent,
    AgentPresetVersion,
    AgentPresetVersionSubagent,
)
from tracecat.db.soft_delete import with_deleted
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService


@dataclass(frozen=True, slots=True, order=True)
class SubagentBindingSpec:
    """Logical subagent edge captured by a preset head or version."""

    child_preset_id: uuid.UUID
    alias: str
    description: str | None
    max_turns: int | None


class SubagentBindingService(BaseWorkspaceService):
    """Read and mutate normalized subagent edges."""

    service_name = "subagent_binding"

    async def get_head_binding(self, preset_id: uuid.UUID) -> ResolvedAgentsConfig:
        """Return the mutable preset head's subagent binding."""

        return await self._get_binding(
            binding_model=AgentPresetSubagent,
            owner_column=AgentPresetSubagent.parent_preset_id,
            owner_id=preset_id,
        )

    async def get_version_binding(
        self, preset_version_id: uuid.UUID
    ) -> ResolvedAgentsConfig:
        """Return an immutable preset version's subagent-head binding."""

        return await self._get_binding(
            binding_model=AgentPresetVersionSubagent,
            owner_column=AgentPresetVersionSubagent.parent_preset_version_id,
            owner_id=preset_version_id,
        )

    async def _get_binding(
        self,
        *,
        binding_model: type[AgentPresetSubagent] | type[AgentPresetVersionSubagent],
        owner_column: Any,
        owner_id: uuid.UUID,
    ) -> ResolvedAgentsConfig:
        stmt = (
            select(
                AgentPreset.id,
                AgentPreset.slug,
                AgentPreset.current_version_id,
                AgentPresetVersion.version,
                binding_model.alias,
                binding_model.description,
                binding_model.max_turns,
            )
            .select_from(binding_model)
            .join(
                AgentPreset,
                sa.and_(
                    AgentPreset.workspace_id == binding_model.workspace_id,
                    AgentPreset.id == binding_model.child_preset_id,
                ),
            )
            .outerjoin(
                AgentPresetVersion,
                sa.and_(
                    AgentPresetVersion.workspace_id == AgentPreset.workspace_id,
                    AgentPresetVersion.id == AgentPreset.current_version_id,
                ),
            )
            .where(
                binding_model.workspace_id == self.workspace_id,
                owner_column == owner_id,
            )
            .order_by(binding_model.alias)
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        subagents: list[ResolvedAttachedSubagentRef] = []
        for (
            child_preset_id,
            child_slug,
            child_version_id,
            child_version,
            alias,
            description,
            max_turns,
        ) in rows:
            if child_version_id is None or child_version is None:
                raise TracecatNotFoundError(
                    f"Current version for subagent preset '{child_preset_id}' not found"
                )
            subagents.append(
                ResolvedAttachedSubagentRef(
                    preset=child_slug,
                    preset_id=child_preset_id,
                    preset_version_id=child_version_id,
                    preset_version=child_version,
                    name=alias,
                    description=description,
                    max_turns=max_turns,
                )
            )
        return ResolvedAgentsConfig(subagents=subagents)

    async def replace_head(
        self,
        preset_id: uuid.UUID,
        binding: ResolvedAgentsConfig,
    ) -> None:
        """Replace one preset head's complete subagent edge set."""

        await self.session.execute(
            sa.delete(AgentPresetSubagent).where(
                AgentPresetSubagent.workspace_id == self.workspace_id,
                AgentPresetSubagent.parent_preset_id == preset_id,
            )
        )
        self.session.add_all(
            [
                AgentPresetSubagent(
                    workspace_id=self.workspace_id,
                    parent_preset_id=preset_id,
                    child_preset_id=subagent.preset_id,
                    alias=subagent.alias,
                    description=subagent.description,
                    max_turns=subagent.max_turns,
                )
                for subagent in binding.subagents
            ]
        )
        await self.session.flush()

    async def snapshot_version(
        self,
        *,
        preset_id: uuid.UUID,
        preset_version_id: uuid.UUID,
    ) -> None:
        """Snapshot the head's logical child edges onto a preset version."""

        stmt = (
            select(AgentPresetSubagent)
            .where(
                AgentPresetSubagent.workspace_id == self.workspace_id,
                AgentPresetSubagent.parent_preset_id == preset_id,
            )
            .order_by(AgentPresetSubagent.alias)
        )
        bindings = (await self.session.execute(stmt)).scalars().all()
        self.session.add_all(
            [
                AgentPresetVersionSubagent(
                    workspace_id=self.workspace_id,
                    parent_preset_version_id=preset_version_id,
                    child_preset_id=binding.child_preset_id,
                    alias=binding.alias,
                    description=binding.description,
                    max_turns=binding.max_turns,
                )
                for binding in bindings
            ]
        )
        await self.session.flush()

    async def restore_head(
        self,
        *,
        preset_id: uuid.UUID,
        preset_version_id: uuid.UUID,
    ) -> None:
        """Replace the mutable head edges from an immutable version snapshot."""

        stmt = (
            select(AgentPresetVersionSubagent)
            .where(
                AgentPresetVersionSubagent.workspace_id == self.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id
                == preset_version_id,
            )
            .order_by(AgentPresetVersionSubagent.alias)
        )
        bindings = (await self.session.execute(stmt)).scalars().all()
        await self.session.execute(
            sa.delete(AgentPresetSubagent).where(
                AgentPresetSubagent.workspace_id == self.workspace_id,
                AgentPresetSubagent.parent_preset_id == preset_id,
            )
        )
        self.session.add_all(
            [
                AgentPresetSubagent(
                    workspace_id=self.workspace_id,
                    parent_preset_id=preset_id,
                    child_preset_id=binding.child_preset_id,
                    alias=binding.alias,
                    description=binding.description,
                    max_turns=binding.max_turns,
                )
                for binding in bindings
            ]
        )
        await self.session.flush()

    async def head_matches_version(
        self,
        *,
        preset_id: uuid.UUID,
        preset_version_id: uuid.UUID,
    ) -> bool:
        """Return whether a version snapshots the current head edge set."""

        head_specs = await self._list_specs(
            binding_model=AgentPresetSubagent,
            owner_column=AgentPresetSubagent.parent_preset_id,
            owner_id=preset_id,
        )
        version_specs = await self._list_specs(
            binding_model=AgentPresetVersionSubagent,
            owner_column=AgentPresetVersionSubagent.parent_preset_version_id,
            owner_id=preset_version_id,
        )
        return head_specs == version_specs

    async def _list_specs(
        self,
        *,
        binding_model: type[AgentPresetSubagent] | type[AgentPresetVersionSubagent],
        owner_column: Any,
        owner_id: uuid.UUID,
    ) -> list[SubagentBindingSpec]:
        stmt = (
            select(
                binding_model.child_preset_id,
                binding_model.alias,
                binding_model.description,
                binding_model.max_turns,
            )
            .where(
                binding_model.workspace_id == self.workspace_id,
                owner_column == owner_id,
            )
            .order_by(binding_model.alias)
        )
        return [
            SubagentBindingSpec(*row)
            for row in (await self.session.execute(stmt)).tuples().all()
        ]

    async def version_ids_with_subagents(
        self, version_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Return the selected version IDs that have at least one subagent edge."""

        if not version_ids:
            return set()
        stmt = select(AgentPresetVersionSubagent.parent_preset_version_id).where(
            AgentPresetVersionSubagent.workspace_id == self.workspace_id,
            AgentPresetVersionSubagent.parent_preset_version_id.in_(version_ids),
        )
        return set((await self.session.execute(stmt)).scalars().all())

    async def preset_ids_with_subagents(
        self, preset_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Return the selected preset IDs that have at least one head edge."""

        if not preset_ids:
            return set()
        stmt = select(AgentPresetSubagent.parent_preset_id).where(
            AgentPresetSubagent.workspace_id == self.workspace_id,
            AgentPresetSubagent.parent_preset_id.in_(preset_ids),
        )
        return set((await self.session.execute(stmt)).scalars().all())
