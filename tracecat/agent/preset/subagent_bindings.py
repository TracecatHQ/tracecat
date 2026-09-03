"""Relational storage for preset-backed subagent head edges."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError
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


type ResolvedBindingRow = tuple[
    uuid.UUID,
    str | None,
    uuid.UUID | None,
    int | None,
    str,
    str | None,
    int | None,
]


class SubagentBindingService(BaseWorkspaceService):
    """Read and mutate normalized subagent edges."""

    service_name = "subagent_binding"

    async def get_head_binding(self, preset_id: uuid.UUID) -> ResolvedAgentsConfig:
        """Return the mutable preset head's subagent binding."""

        return await self._get_binding(
            binding_model=AgentPresetSubagent,
            owner_column=AgentPresetSubagent.parent_preset_id,
            owner_id=preset_id,
            compatibility_model=AgentPreset,
            compatibility_owner_column=AgentPreset.id,
        )

    async def get_version_binding(
        self, preset_version_id: uuid.UUID
    ) -> ResolvedAgentsConfig:
        """Return an immutable preset version's subagent-head binding."""

        return await self._get_binding(
            binding_model=AgentPresetVersionSubagent,
            owner_column=AgentPresetVersionSubagent.parent_preset_version_id,
            owner_id=preset_version_id,
            compatibility_model=AgentPresetVersion,
            compatibility_owner_column=AgentPresetVersion.id,
        )

    async def _get_binding(
        self,
        *,
        binding_model: type[AgentPresetSubagent] | type[AgentPresetVersionSubagent],
        owner_column: Any,
        owner_id: uuid.UUID,
        compatibility_model: type[AgentPreset] | type[AgentPresetVersion],
        compatibility_owner_column: Any,
    ) -> ResolvedAgentsConfig:
        compatibility_specs = await self._compatibility_specs(
            owner_model=compatibility_model,
            owner_column=compatibility_owner_column,
            owner_id=owner_id,
        )
        if compatibility_specs is not None:
            return await self._resolve_specs(compatibility_specs)

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
        return self._rows_to_binding(rows)

    async def _resolve_specs(
        self, specs: list[SubagentBindingSpec]
    ) -> ResolvedAgentsConfig:
        """Resolve compatibility specs against each child's current head."""

        if not specs:
            return ResolvedAgentsConfig()
        child_ids = {spec.child_preset_id for spec in specs}
        stmt = (
            select(
                AgentPreset.id,
                AgentPreset.slug,
                AgentPreset.current_version_id,
                AgentPresetVersion.version,
            )
            .select_from(AgentPreset)
            .outerjoin(
                AgentPresetVersion,
                sa.and_(
                    AgentPresetVersion.workspace_id == AgentPreset.workspace_id,
                    AgentPresetVersion.id == AgentPreset.current_version_id,
                ),
            )
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id.in_(child_ids),
            )
        )
        children = {
            child_id: (slug, version_id, version)
            for child_id, slug, version_id, version in (
                await self.session.execute(with_deleted(stmt))
            )
            .tuples()
            .all()
        }
        rows: list[ResolvedBindingRow] = []
        for spec in sorted(specs, key=lambda item: item.alias):
            if child := children.get(spec.child_preset_id):
                child_slug, child_version_id, child_version = child
            else:
                child_slug = None
                child_version_id = None
                child_version = None
            rows.append(
                (
                    spec.child_preset_id,
                    child_slug,
                    child_version_id,
                    child_version,
                    spec.alias,
                    spec.description,
                    spec.max_turns,
                )
            )
        return self._rows_to_binding(rows)

    @staticmethod
    def _rows_to_binding(rows: Sequence[ResolvedBindingRow]) -> ResolvedAgentsConfig:
        """Convert resolved edge rows into the public binding model."""

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
            if child_slug is None or child_version_id is None or child_version is None:
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

    async def _compatibility_specs(
        self,
        *,
        owner_model: type[AgentPreset] | type[AgentPresetVersion],
        owner_column: Any,
        owner_id: uuid.UUID,
    ) -> list[SubagentBindingSpec] | None:
        """Read the JSON shadow used as authority during the expand window."""

        stmt = select(owner_model.agents).where(
            owner_model.workspace_id == self.workspace_id,
            owner_column == owner_id,
        )
        agents = (await self.session.execute(with_deleted(stmt))).scalar_one_or_none()
        return self._specs_from_compatibility_json(agents)

    @staticmethod
    def _specs_from_compatibility_json(
        agents: dict[str, Any] | None,
    ) -> list[SubagentBindingSpec] | None:
        """Parse a resolved JSON shadow, or defer to rows for legacy shapes."""

        if not isinstance(agents, dict):
            return None
        raw_subagents = agents.get("subagents", [])
        if not isinstance(raw_subagents, list):
            return None
        try:
            binding = ResolvedAgentsConfig.model_validate({"subagents": raw_subagents})
        except ValidationError:
            return None
        return [
            SubagentBindingSpec(
                child_preset_id=subagent.preset_id,
                alias=subagent.alias,
                description=subagent.description,
                max_turns=subagent.max_turns,
            )
            for subagent in binding.subagents
        ]

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
        await self.session.execute(
            sa.update(AgentPreset)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id == preset_id,
            )
            .values(agents=binding.model_dump(mode="json"))
        )
        await self.session.flush()

    async def snapshot_version(
        self,
        *,
        preset_id: uuid.UUID,
        preset_version_id: uuid.UUID,
    ) -> None:
        """Snapshot the head's logical child edges onto a preset version."""

        binding = await self.get_head_binding(preset_id)
        # An old pod can update only the JSON shadow during a rolling deploy.
        # Reconcile the mutable rows whenever the new code publishes a version.
        await self.replace_head(preset_id, binding)
        self.session.add_all(
            [
                AgentPresetVersionSubagent(
                    workspace_id=self.workspace_id,
                    parent_preset_version_id=preset_version_id,
                    child_preset_id=subagent.preset_id,
                    alias=subagent.alias,
                    description=subagent.description,
                    max_turns=subagent.max_turns,
                )
                for subagent in binding.subagents
            ]
        )
        await self.session.execute(
            sa.update(AgentPresetVersion)
            .where(
                AgentPresetVersion.workspace_id == self.workspace_id,
                AgentPresetVersion.id == preset_version_id,
            )
            .values(agents=binding.model_dump(mode="json"))
        )
        await self.session.flush()

    async def restore_head(
        self,
        *,
        preset_id: uuid.UUID,
        preset_version_id: uuid.UUID,
    ) -> None:
        """Replace the mutable head edges from an immutable version snapshot."""

        binding = await self.get_version_binding(preset_version_id)
        await self.replace_head(preset_id, binding)

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
        if binding_model is AgentPresetSubagent:
            compatibility_specs = await self._compatibility_specs(
                owner_model=AgentPreset,
                owner_column=AgentPreset.id,
                owner_id=owner_id,
            )
        else:
            compatibility_specs = await self._compatibility_specs(
                owner_model=AgentPresetVersion,
                owner_column=AgentPresetVersion.id,
                owner_id=owner_id,
            )
        if compatibility_specs is not None:
            return sorted(compatibility_specs)

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
        return await self._owner_ids_with_subagents(
            owner_model=AgentPresetVersion,
            owner_column=AgentPresetVersion.id,
            binding_model=AgentPresetVersionSubagent,
            binding_owner_column=AgentPresetVersionSubagent.parent_preset_version_id,
            owner_ids=version_ids,
        )

    async def preset_ids_with_subagents(
        self, preset_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Return the selected preset IDs that have at least one head edge."""

        if not preset_ids:
            return set()
        return await self._owner_ids_with_subagents(
            owner_model=AgentPreset,
            owner_column=AgentPreset.id,
            binding_model=AgentPresetSubagent,
            binding_owner_column=AgentPresetSubagent.parent_preset_id,
            owner_ids=preset_ids,
        )

    async def _owner_ids_with_subagents(
        self,
        *,
        owner_model: type[AgentPreset] | type[AgentPresetVersion],
        owner_column: Any,
        binding_model: type[AgentPresetSubagent] | type[AgentPresetVersionSubagent],
        binding_owner_column: Any,
        owner_ids: list[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Return owners with subagents, honoring the JSON compatibility shadow."""

        owner_stmt = select(owner_column, owner_model.agents).where(
            owner_model.workspace_id == self.workspace_id,
            owner_column.in_(owner_ids),
        )
        owners = (await self.session.execute(with_deleted(owner_stmt))).tuples().all()
        result: set[uuid.UUID] = set()
        fallback_owner_ids: list[uuid.UUID] = []
        for owner_id, agents in owners:
            specs = self._specs_from_compatibility_json(agents)
            if specs is None:
                fallback_owner_ids.append(owner_id)
            elif specs:
                result.add(owner_id)
        if fallback_owner_ids:
            binding_stmt = select(binding_owner_column).where(
                binding_model.workspace_id == self.workspace_id,
                binding_owner_column.in_(fallback_owner_ids),
            )
            result.update((await self.session.execute(binding_stmt)).scalars().all())
        return result
