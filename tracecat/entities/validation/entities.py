"""Entity-level validators.

Scopes entity operations to the current workspace and provides
existence, activeness, and uniqueness checks with friendly errors.
"""

from typing import cast
from uuid import UUID

from pydantic_core import PydanticCustomError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Entity
from tracecat.identifiers import WorkspaceID
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError


class EntityValidators:
    """Database-dependent validators for entity-level operations.

    - validate_entity_exists: ensure an entity exists in this workspace
    - validate_entity_active: ensure the entity is not deactivated
    - validate_entity_name_unique: pre-check name uniqueness for better UX
    """

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        self.session = session
        self.workspace_id = workspace_id

    async def validate_entity_exists(
        self, entity_id: UUID, raise_on_missing: bool = True
    ) -> Entity | None:
        """Return the entity if it exists (and belongs to workspace).

        Raises TracecatNotFoundError when not found and raise_on_missing is True.
        """
        stmt = select(Entity).where(
            Entity.id == entity_id,
            Entity.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        entity = result.first()

        if not entity and raise_on_missing:
            raise TracecatNotFoundError(f"Entity with ID {entity_id} not found")
        return entity

    async def validate_entity_name_unique(
        self, name: str, exclude_id: UUID | None = None
    ) -> None:
        """Pre-check entity name uniqueness for friendlier errors.

        DB constraints also protect this; this guard improves UX by surfacing
        a precise error before hitting an IntegrityError.
        """
        stmt = select(Entity).where(
            Entity.name == name,
            Entity.owner_id == self.workspace_id,
        )
        if exclude_id:
            stmt = stmt.where(Entity.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first():
            raise PydanticCustomError(
                "unique_violation",
                "Entity name '{name}' already exists",
                {"name": name},
            )

    async def validate_entity_active(self, entity_id: UUID) -> Entity:
        """Ensure an entity exists and is active; return it."""
        entity = cast(Entity, await self.validate_entity_exists(entity_id))
        if not entity.is_active:
            raise TracecatValidationError(f"Entity {entity.name} is not active")
        return entity
