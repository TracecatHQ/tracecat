"""Relation validators with workspace policy enforcement.

Policies supported via workspace.settings:
- relation_policy: "unrestricted" | "allow_one_level" | "block_cycles" | "max_degree"
- relation_max_degree: optional int used when policy == "max_degree"
"""

from uuid import UUID

import sqlalchemy as sa
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import RelationDefinition
from tracecat.identifiers import WorkspaceID
from tracecat.types.exceptions import TracecatValidationError


class RelationValidators:
    """Database-dependent validators for relation operations with policy enforcement."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
    ):
        self.session = session
        self.workspace_id = workspace_id

    async def get_workspace_relation_policy(self) -> tuple[str, int | None]:
        """Return (policy_type, max_degree) from workspace settings.

        Defaults to ("unrestricted", None) when not set.
        """
        from tracecat.db.schemas import Workspace

        stmt = select(Workspace).where(Workspace.id == self.workspace_id)
        result = await self.session.exec(stmt)
        workspace = result.first()

        if not workspace or not workspace.settings:
            return ("unrestricted", None)

        policy = (
            workspace.settings.get("relation_policy", "unrestricted") or "unrestricted"
        )
        max_degree = workspace.settings.get("relation_max_degree")
        return (policy, max_degree)

    async def validate_relation_creation(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
    ) -> None:
        """Validate relation creation against workspace policies."""
        policy, max_degree = await self.get_workspace_relation_policy()

        if policy == "unrestricted":
            return

        # For self-referential relations, optionally block cycles
        if source_entity_id == target_entity_id and policy in (
            "block_cycles",
            "allow_one_level",
        ):
            await self._validate_no_cycles(source_entity_id, target_entity_id)

        if policy == "allow_one_level":
            await self._validate_one_level_only(source_entity_id, target_entity_id)
        elif policy == "max_degree" and max_degree is not None:
            await self._validate_max_degree(
                source_entity_id, target_entity_id, max_degree
            )

    async def _validate_no_cycles(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
    ) -> None:
        """Raise if creating source->target would create a cycle in the entity graph."""
        visited: set[UUID] = set()
        to_visit: list[UUID] = [target_entity_id]

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            if current == source_entity_id:
                raise TracecatValidationError(
                    "Creating this relation would create a cycle in the relation graph"
                )

            stmt = select(RelationDefinition.target_entity_id).where(
                RelationDefinition.source_entity_id == current,
                RelationDefinition.owner_id == self.workspace_id,
                RelationDefinition.is_active == sa.true(),
            )
            result = await self.session.exec(stmt)
            for target_id in result:
                if target_id not in visited:
                    to_visit.append(target_id)

    async def _validate_one_level_only(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
    ) -> None:
        """Disallow creating transitive relations by enforcing one-level graphs."""
        stmt = select(RelationDefinition).where(
            RelationDefinition.source_entity_id == source_entity_id,
            RelationDefinition.owner_id == self.workspace_id,
            RelationDefinition.is_active == sa.true(),
        )
        source_relations = (await self.session.exec(stmt)).all()

        stmt = select(RelationDefinition).where(
            RelationDefinition.target_entity_id == target_entity_id,
            RelationDefinition.owner_id == self.workspace_id,
            RelationDefinition.is_active == sa.true(),
        )
        target_relations = (await self.session.exec(stmt)).all()

        if source_relations or target_relations:
            raise TracecatValidationError(
                "Workspace policy only allows one level of relations. Cannot create relations between entities that already have relations."
            )

    async def _validate_max_degree(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
        max_degree: int,
    ) -> None:
        """Enforce a maximum in- and out-degree for relations."""
        stmt = (
            select(sa.func.count())
            .select_from(RelationDefinition)
            .where(
                RelationDefinition.source_entity_id == source_entity_id,
                RelationDefinition.owner_id == self.workspace_id,
                RelationDefinition.is_active == sa.true(),
            )
        )
        outgoing_count = (await self.session.exec(stmt)).first() or 0
        if outgoing_count >= max_degree:
            raise TracecatValidationError(
                f"Entity already has {outgoing_count} outgoing relations. Maximum allowed is {max_degree}."
            )

        stmt = (
            select(sa.func.count())
            .select_from(RelationDefinition)
            .where(
                RelationDefinition.target_entity_id == target_entity_id,
                RelationDefinition.owner_id == self.workspace_id,
                RelationDefinition.is_active == sa.true(),
            )
        )
        incoming_count = (await self.session.exec(stmt)).first() or 0
        if incoming_count >= max_degree:
            raise TracecatValidationError(
                f"Target entity already has {incoming_count} incoming relations. Maximum allowed is {max_degree}."
            )
