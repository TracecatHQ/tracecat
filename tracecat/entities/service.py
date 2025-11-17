import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.auth.types import Role
from tracecat.db.models import Entity, EntityField, EntityFieldOption
from tracecat.entities.schemas import (
    EntityCreate,
    EntityFieldCreate,
    EntityFieldUpdate,
    EntityUpdate,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService


class EntityService(BaseWorkspaceService):
    """Service for managing Entities and their Fields within a workspace."""

    service_name = "entities"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.fields = EntityFieldsService(session=self.session, role=self.role)

    async def list_entities(
        self, *, include_inactive: bool = False
    ) -> Sequence[Entity]:
        stmt = select(Entity).where(Entity.owner_id == self.workspace_id)
        if not include_inactive:
            stmt = stmt.where(Entity.is_active)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_entity(self, entity_id: uuid.UUID) -> Entity:
        stmt = select(Entity).where(
            Entity.owner_id == self.workspace_id,
            Entity.id == entity_id,
        )
        result = await self.session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            raise TracecatNotFoundError("Entity not found")
        return entity

    async def get_entity_by_key(self, key: str) -> Entity:
        stmt = select(Entity).where(
            Entity.owner_id == self.workspace_id,
            Entity.key == key,
        )
        result = await self.session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            raise TracecatNotFoundError("Entity not found")
        return entity

    async def create_entity(self, params: EntityCreate) -> Entity:
        entity = Entity(
            owner_id=self.workspace_id,
            key=params.key,
            display_name=params.display_name,
            description=params.description,
            icon=params.icon,
            is_active=True,
        )
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def update_entity(self, entity: Entity, params: EntityUpdate) -> Entity:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        set_fields = params.model_dump(exclude_unset=True)
        for key, value in set_fields.items():
            setattr(entity, key, value)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def deactivate_entity(self, entity: Entity) -> Entity:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")
        if not entity.is_active:
            return entity
        entity.is_active = False
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def activate_entity(self, entity: Entity) -> Entity:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")
        if entity.is_active:
            return entity
        entity.is_active = True
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def delete_entity(self, entity: Entity) -> None:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        await self.session.delete(entity)
        await self.session.commit()


class EntityFieldsService(BaseWorkspaceService):
    """Service for managing Entity Fields within a workspace."""

    service_name = "entity_fields"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    async def list_fields(
        self, entity: Entity, *, include_inactive: bool = False
    ) -> Sequence[EntityField]:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        stmt = (
            select(EntityField)
            .where(EntityField.entity_id == entity.id)
            .options(selectinload(EntityField.options))
        )
        if not include_inactive:
            stmt = stmt.where(EntityField.is_active)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_field(self, entity: Entity, field_id: uuid.UUID) -> EntityField:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        stmt = (
            select(EntityField)
            .where(
                EntityField.entity_id == entity.id,
                EntityField.id == field_id,
            )
            .options(selectinload(EntityField.options))
        )
        result = await self.session.execute(stmt)
        field = result.scalars().first()
        if field is None:
            raise TracecatNotFoundError("Field not found")
        return field

    async def get_field_by_key(self, entity: Entity, key: str) -> EntityField:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        stmt = (
            select(EntityField)
            .where(
                EntityField.entity_id == entity.id,
                EntityField.key == key,
            )
            .options(selectinload(EntityField.options))
        )
        result = await self.session.execute(stmt)
        field = result.scalars().first()
        if field is None:
            raise TracecatNotFoundError("Field not found")
        return field

    async def create_field(
        self, entity: Entity, params: EntityFieldCreate
    ) -> EntityField:
        # Ensure workspace ownership
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        field = EntityField(
            owner_id=self.workspace_id,
            entity_id=entity.id,
            key=params.key,
            type=params.type,
            display_name=params.display_name,
            description=params.description,
            default_value=params.default_value,
            is_active=True,
        )

        # Handle options for SELECT/MULTI_SELECT
        if params.options:
            # Prefer relationship assignment for clarity; include field_id for type checkers
            field.options = [
                EntityFieldOption(
                    field_id=field.id,
                    field=field,
                    key=opt.resolved_key,
                    label=opt.label,
                )
                for opt in params.options
            ]

        self.session.add(field)
        await self.session.commit()
        # Re-fetch with options eagerly loaded
        stmt = (
            select(EntityField)
            .where(EntityField.id == field.id)
            .options(selectinload(EntityField.options))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def update_field(
        self, field: EntityField, params: EntityFieldUpdate
    ) -> EntityField:
        # Ensure workspace ownership
        if field.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Field not found")

        # Update simple fields (immutable: key, type)
        set_fields = params.model_dump(exclude_unset=True)

        if "display_name" in set_fields:
            field.display_name = set_fields["display_name"]
        if "description" in set_fields:
            field.description = set_fields["description"]
        if "default_value" in set_fields:
            # Coerce and validate default value according to field type
            from tracecat.entities.schemas import coerce_default_value

            field.default_value = coerce_default_value(
                field.type, set_fields["default_value"]
            )

        # Handle options update if provided (PATCH behavior: add/update/remove)
        if params.options is not None:
            # Ensure we have a managed instance with options loaded
            reload_stmt = (
                select(EntityField)
                .where(EntityField.id == field.id)
                .options(selectinload(EntityField.options))
            )
            field = (await self.session.execute(reload_stmt)).scalar_one()

            existing_by_key = {opt.key: opt for opt in field.options}
            # Normalize/generated keys are ensured by the option model
            new_by_key = {opt.resolved_key: opt for opt in params.options}

            # Build new relationship collection preserving existing rows when possible
            next_options: list[EntityFieldOption] = []
            for key, new_opt in new_by_key.items():
                if key in existing_by_key:
                    db_opt = existing_by_key[key]
                    if db_opt.label != new_opt.label:
                        db_opt.label = new_opt.label
                    next_options.append(db_opt)
                else:
                    next_options.append(
                        EntityFieldOption(
                            field_id=field.id,
                            field=field,
                            key=new_opt.resolved_key,
                            label=new_opt.label,
                        )
                    )

            # Assigning the list will trigger delete-orphan for removed options
            field.options = next_options

        await self.session.commit()
        # Return re-fetched field with options eagerly loaded
        stmt = (
            select(EntityField)
            .where(EntityField.id == field.id)
            .options(selectinload(EntityField.options))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def deactivate_field(self, field: EntityField) -> EntityField:
        # Ensure workspace ownership
        if field.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Field not found")
        if not field.is_active:
            return field
        field.is_active = False
        await self.session.commit()
        await self.session.refresh(field)
        return field

    async def activate_field(self, field: EntityField) -> EntityField:
        # Ensure workspace ownership
        if field.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Field not found")
        if field.is_active:
            return field
        field.is_active = True
        await self.session.commit()
        await self.session.refresh(field)
        return field

    async def delete_field(self, field: EntityField) -> None:
        # Ensure workspace ownership
        if field.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Field not found")

        await self.session.delete(field)
        await self.session.commit()
