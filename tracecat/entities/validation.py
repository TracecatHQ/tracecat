"""Validators for custom entities.

This module provides both pure validation functions and database-dependent validators.
Pure functions are at module level, database-dependent validators are in classes.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from pydantic_core import PydanticCustomError
from sqlalchemy.sql import ColumnElement
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    Entity,
    FieldMetadata,
    Record,
    RecordRelationLink,
    RelationDefinition,
)
from tracecat.entities.enums import RelationType
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import (
    FieldType,
    validate_field_value_type,
    validate_flat_structure,
)
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError

# Relation nesting policy and validator removed (replaced by graph-based guardrails later)


# Pure validation functions (no database dependencies)


def validate_enum_options(options: list[str] | None) -> list[str] | None:
    """Validate enum options are unique and non-empty.

    Args:
        options: List of enum options to validate

    Returns:
        Validated options or None

    Raises:
        PydanticCustomError: If validation fails
    """
    if options is None:
        return None

    # Ensure all options are unique
    if len(set(options)) != len(options):
        raise PydanticCustomError(
            "duplicate_enum_options",
            "Enum options must be unique",
        )

    # Ensure options are non-empty strings
    for opt in options:
        if not opt or not opt.strip():
            raise PydanticCustomError(
                "empty_enum_option",
                "Enum options cannot be empty strings",
            )

    return options


def validate_default_value_type(
    value: Any, field_type: FieldType, enum_options: list[str] | None = None
) -> Any:
    """Validate a default value matches the field type.

    This function:
    1. Checks if the field type supports default values
    2. Validates the value matches the field type
    3. Checks for flat structure (no nested objects)

    Args:
        value: The default value to validate
        field_type: The field type
        enum_options: Options for SELECT/MULTI_SELECT fields

    Returns:
        Validated value

    Raises:
        PydanticCustomError: If validation fails
    """
    if value is None:
        return None

    # Check if field type supports default values
    unsupported_types = {
        FieldType.ARRAY_TEXT,
        FieldType.ARRAY_INTEGER,
        FieldType.ARRAY_NUMBER,
        FieldType.DATE,
        FieldType.DATETIME,
    }

    if field_type in unsupported_types:
        raise PydanticCustomError(
            "default_not_supported",
            "Field type '{field_type}' does not support default values",
            {"field_type": field_type.value},
        )

    # Check for acceptable structure (now allows nested objects with depth limit)
    if not validate_flat_structure(value):
        raise PydanticCustomError(
            "invalid_structure",
            "Default values cannot contain nested arrays or exceed 3 levels of nesting",
        )

    # Validate the value against the field type
    # This will raise PydanticCustomError if invalid
    return validate_field_value_type(value, field_type, enum_options)


def validate_relation_uuid(
    value: Any, allow_none: bool = False, context: str = "relation"
) -> UUID | None:
    """Validate and convert a value to UUID for relations.

    Args:
        value: Value to validate (UUID, string, or None)
        allow_none: Whether None is allowed
        context: Context for error messages

    Returns:
        UUID object or None

    Raises:
        PydanticCustomError: If validation fails
    """
    if value is None:
        if allow_none:
            return None
        raise PydanticCustomError(
            "null_not_allowed",
            "{context} cannot be null",
            {"context": context},
        )

    if isinstance(value, UUID):
        return value

    if isinstance(value, str):
        try:
            return UUID(value)
        except (ValueError, TypeError) as e:
            raise PydanticCustomError(
                "invalid_uuid",
                "Invalid UUID format for {context}",
                {"context": context},
            ) from e

    raise PydanticCustomError(
        "invalid_type",
        "Expected UUID or string for {context}, got {type_name}",
        {"context": context, "type_name": type(value).__name__},
    )


class EntityValidators:
    """Database-dependent validators for entity-level operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        """Initialize entity validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id

    async def validate_entity_exists(
        self, entity_id: UUID, raise_on_missing: bool = True
    ) -> Entity | None:
        """Validate that an entity exists in the workspace.

        Args:
            entity_id: Entity metadata ID to check
            raise_on_missing: Whether to raise exception if not found

        Returns:
            Entity if found, None if not found and not raising

        Raises:
            TracecatNotFoundError: If entity not found and raise_on_missing=True
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
        """Check if entity name is unique in workspace.

        Args:
            name: Entity name to check
            exclude_id: Entity ID to exclude (for updates)

        Raises:
            PydanticCustomError: If name already exists
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
        """Validate that an entity is active.

        Args:
            entity_id: Entity metadata ID to check

        Returns:
            Entity if active

        Raises:
            TracecatValidationError: If entity is not active
            TracecatNotFoundError: If entity not found
        """
        entity = await self.validate_entity_exists(entity_id)
        if not entity:
            raise TracecatNotFoundError(f"Entity with ID {entity_id} not found")
        if not entity.is_active:
            raise TracecatValidationError(f"Entity {entity.name} is not active")
        return entity


class FieldValidators:
    """Database-dependent validators for field-level operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        """Initialize field validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id

    async def validate_field_exists(
        self, field_id: UUID, raise_on_missing: bool = True
    ) -> FieldMetadata | None:
        """Validate that a field exists.

        Args:
            field_id: Field metadata ID to check
            raise_on_missing: Whether to raise exception if not found

        Returns:
            FieldMetadata if found, None if not found and not raising

        Raises:
            TracecatNotFoundError: If field not found and raise_on_missing=True
        """
        # Join with Entity to check workspace ownership
        stmt = (
            select(FieldMetadata)
            .join(
                Entity,
                cast(ColumnElement[Any], FieldMetadata.entity_id)
                == cast(ColumnElement[Any], Entity.id),
            )
            .where(
                FieldMetadata.id == field_id,
                Entity.owner_id == self.workspace_id,
            )
        )
        result = await self.session.exec(stmt)
        field = result.first()

        if not field and raise_on_missing:
            raise TracecatNotFoundError(f"Field with ID {field_id} not found")
        return field

    async def validate_field_key_unique(
        self,
        entity_id: UUID,
        field_key: str,
        exclude_id: UUID | None = None,
    ) -> None:
        """Check if field key is unique within entity.

        Args:
            entity_id: Entity metadata ID
            field_key: Field key to check
            exclude_id: Field ID to exclude (for updates)

        Raises:
            PydanticCustomError: If field key already exists
        """
        # Join with Entity to check workspace ownership
        stmt = (
            select(FieldMetadata)
            .join(
                Entity,
                cast(ColumnElement[Any], FieldMetadata.entity_id)
                == cast(ColumnElement[Any], Entity.id),
            )
            .where(
                FieldMetadata.entity_id == entity_id,
                FieldMetadata.field_key == field_key,
                Entity.owner_id == self.workspace_id,
            )
        )
        if exclude_id:
            stmt = stmt.where(FieldMetadata.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first():
            raise PydanticCustomError(
                "unique_violation",
                "Field key '{field_key}' already exists in entity",
                {"field_key": field_key},
            )

    async def validate_field_active(self, field_id: UUID) -> FieldMetadata:
        """Validate that a field is active.

        Args:
            field_id: Field metadata ID to check

        Returns:
            FieldMetadata if active

        Raises:
            TracecatValidationError: If field is not active
            TracecatNotFoundError: If field not found
        """
        field = await self.validate_field_exists(field_id)
        if not field:
            raise TracecatNotFoundError(f"Field with ID {field_id} not found")
        if not field.is_active:
            raise TracecatValidationError(f"Field {field.field_key} is not active")
        return field


class RecordValidators:
    """Database-dependent validators for record-level operations."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
        query_builder: EntityQueryBuilder | None = None,
    ):
        """Initialize record validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
            query_builder: Optional query builder for complex queries
        """
        self.session = session
        self.workspace_id = workspace_id
        self.query_builder = query_builder or EntityQueryBuilder(session)

    async def validate_record_exists(
        self,
        record_id: UUID,
        entity_id: UUID | None = None,
        raise_on_missing: bool = True,
    ) -> Record | None:
        """Validate that a record exists.

        Args:
            record_id: Record ID to check
            entity_id: Optional entity ID to verify record belongs to entity
            raise_on_missing: Whether to raise exception if not found

        Returns:
            Record if found, None if not found and not raising

        Raises:
            TracecatNotFoundError: If record not found and raise_on_missing=True
        """
        stmt = select(Record).where(
            Record.id == record_id,
            Record.owner_id == self.workspace_id,
        )
        if entity_id:
            stmt = stmt.where(Record.entity_id == entity_id)

        result = await self.session.exec(stmt)
        record = result.first()

        if not record and raise_on_missing:
            raise TracecatNotFoundError(f"Record with ID {record_id} not found")
        return record

    async def validate_record_data(
        self,
        data: dict[str, Any],
        fields: list[FieldMetadata],
        relations: Mapping[str, RelationDefinition] | None = None,
    ) -> dict[str, Any]:
        """Validate record data against field definitions.

        Args:
            data: Record data to validate
            fields: Field definitions

        Returns:
            Validated data dict

        Raises:
            TracecatValidationError: If validation fails
        """
        errors = []
        validated = {}

        active_fields = {f.field_key: f for f in fields if f.is_active}
        relations = relations or {}

        # Check for acceptable structure - only for active fields
        for key, value in data.items():
            if key in active_fields:
                if not validate_flat_structure(value):
                    errors.append(
                        f"Field '{key}': Nested arrays or excessive nesting (>3 levels) not allowed"
                    )

        if errors:
            raise TracecatValidationError("; ".join(errors))

        for key, value in data.items():
            # Regular field validation
            if key in active_fields:
                field = active_fields[key]
                if value is not None:
                    try:
                        validate_field_value_type(
                            value, FieldType(field.field_type), field.enum_options
                        )
                    except PydanticCustomError as e:
                        errors.append(f"Field '{key}': {e.message()}")
                        continue
                validated[key] = value
                continue

            # Relation value validation
            if key in relations:
                rel_def = relations[key]
                if value is not None:
                    is_valid, error = await self._validate_relation_value(
                        rel_def, value
                    )
                    if not is_valid:
                        errors.append(f"Field '{key}': {error}")
                        continue
                validated[key] = value
                continue

            # Unknown key: skip silently
            continue

        if errors:
            raise TracecatValidationError("; ".join(errors))

        return validated

    async def _validate_relation_value(
        self, rel_def: RelationDefinition, value: Any
    ) -> tuple[bool, str | None]:
        """Validate a relation field value.

        Args:
            field: Field metadata with relation info
            value: Value to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        rt = RelationType(rel_def.relation_type)
        if rt in (RelationType.ONE_TO_ONE, RelationType.MANY_TO_ONE):
            # Allow dict for inline creation or a single UUID
            if isinstance(value, dict):
                if not value:
                    return False, "Empty dict not allowed for relation field"
                return True, None
            try:
                validate_relation_uuid(value, allow_none=False, context="relation")
                return True, None
            except PydanticCustomError as e:
                return False, e.message()

        # ONE_TO_MANY / MANY_TO_MANY expects list of UUIDs or dicts
        if not isinstance(value, list):
            return (
                False,
                f"Expected list for relation, got {type(value).__name__}",
            )
        for idx, item in enumerate(value):
            if isinstance(item, dict):
                if not item:
                    return False, f"Item at index {idx}: Empty dict not allowed"
                continue
            try:
                validate_relation_uuid(
                    item, allow_none=False, context=f"item at index {idx}"
                )
            except PydanticCustomError as e:
                return False, f"Item at index {idx}: {e.message()}"

        return True, None


"""Relation nesting validator removed in favor of simple guardrails in future."""


@dataclass
class UpdateStep:
    """Single update operation in the update plan."""

    record_id: UUID
    entity_id: UUID
    field_updates: dict[str, Any]  # Regular field updates
    depth: int  # Nesting depth for this update


@dataclass
class UpdatePlan:
    """Execution plan for nested updates."""

    steps: list[UpdateStep] = field(default_factory=list)
    visited_records: set[UUID] = field(default_factory=set)
    relation_links: dict[tuple[UUID, UUID], UUID] = field(
        default_factory=dict
    )  # (record_id, field_id) -> target_record_id


class RelationValidators:
    """Database-dependent validators for relation operations with policy enforcement."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
    ):
        """Initialize relation validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id

    async def get_workspace_relation_policy(self) -> tuple[str, int | None]:
        """Get workspace relation policy settings.

        Returns:
            Tuple of (policy_type, max_degree) where:
            - policy_type: "unrestricted", "allow_one_level", "block_cycles", "max_degree"
            - max_degree: Maximum degree for "max_degree" policy, None otherwise
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
        relation_type: str,
    ) -> None:
        """Validate relation creation against workspace policies.

        Args:
            source_entity_id: Source entity ID
            target_entity_id: Target entity ID
            relation_type: Type of relation being created

        Raises:
            TracecatValidationError: If relation violates workspace policy
        """
        policy, max_degree = await self.get_workspace_relation_policy()

        if policy == "unrestricted":
            return

        # Check for self-referential relations
        if source_entity_id == target_entity_id:
            if policy in ("block_cycles", "allow_one_level"):
                # Check if this would create a cycle
                await self._validate_no_cycles(source_entity_id, target_entity_id)

        if policy == "allow_one_level":
            # Only allow direct relations, no transitive relations
            await self._validate_one_level_only(source_entity_id, target_entity_id)

        elif policy == "max_degree" and max_degree is not None:
            # Check if this would exceed the maximum degree
            await self._validate_max_degree(
                source_entity_id, target_entity_id, max_degree
            )

    async def _validate_no_cycles(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
    ) -> None:
        """Validate that creating a relation won't create a cycle.

        Args:
            source_entity_id: Source entity ID
            target_entity_id: Target entity ID

        Raises:
            TracecatValidationError: If relation would create a cycle
        """

        # Check if there's already a path from target to source
        # This would create a cycle if we add source -> target
        visited = set()
        to_visit = [target_entity_id]

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            if current == source_entity_id:
                raise TracecatValidationError(
                    "Creating this relation would create a cycle in the relation graph"
                )

            # Find all entities that current points to
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
        """Validate that entities only have direct relations (no transitive).

        Args:
            source_entity_id: Source entity ID
            target_entity_id: Target entity ID

        Raises:
            TracecatValidationError: If relation would create transitive relations
        """

        # Check if source already has any relations
        stmt = select(RelationDefinition).where(
            RelationDefinition.source_entity_id == source_entity_id,
            RelationDefinition.owner_id == self.workspace_id,
            RelationDefinition.is_active == sa.true(),
        )
        result = await self.session.exec(stmt)
        source_relations = result.all()

        # Check if target already has any relations
        stmt = select(RelationDefinition).where(
            RelationDefinition.target_entity_id == target_entity_id,
            RelationDefinition.owner_id == self.workspace_id,
            RelationDefinition.is_active == sa.true(),
        )
        result = await self.session.exec(stmt)
        target_relations = result.all()

        # If either has existing relations, this would create a transitive relation
        if source_relations or target_relations:
            raise TracecatValidationError(
                "Workspace policy only allows one level of relations. "
                "Cannot create relations between entities that already have relations."
            )

    async def _validate_max_degree(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
        max_degree: int,
    ) -> None:
        """Validate that relation doesn't exceed maximum degree.

        Args:
            source_entity_id: Source entity ID
            target_entity_id: Target entity ID
            max_degree: Maximum allowed degree

        Raises:
            TracecatValidationError: If relation would exceed max degree
        """

        # Count existing outgoing relations from source
        stmt = (
            select(sa.func.count())
            .select_from(RelationDefinition)
            .where(
                RelationDefinition.source_entity_id == source_entity_id,
                RelationDefinition.owner_id == self.workspace_id,
                RelationDefinition.is_active == sa.true(),
            )
        )
        result = await self.session.exec(stmt)
        outgoing_count = result.first() or 0

        if outgoing_count >= max_degree:
            raise TracecatValidationError(
                f"Entity already has {outgoing_count} outgoing relations. "
                f"Maximum allowed is {max_degree}."
            )

        # Count existing incoming relations to target
        stmt = (
            select(sa.func.count())
            .select_from(RelationDefinition)
            .where(
                RelationDefinition.target_entity_id == target_entity_id,
                RelationDefinition.owner_id == self.workspace_id,
                RelationDefinition.is_active == sa.true(),
            )
        )
        result = await self.session.exec(stmt)
        incoming_count = result.first() or 0

        if incoming_count >= max_degree:
            raise TracecatValidationError(
                f"Target entity already has {incoming_count} incoming relations. "
                f"Maximum allowed is {max_degree}."
            )


class NestedUpdateValidator:
    """Validator for nested relation updates during record updates."""

    # Configuration limits
    MAX_RELATION_DEPTH = 2  # Maximum depth for nested relation updates
    MAX_JSON_DEPTH = 5  # Maximum depth for JSON structure in fields
    MAX_UPDATE_SIZE = 100  # Maximum number of fields in single update

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
        query_builder: EntityQueryBuilder | None = None,
    ):
        """Initialize nested update validator.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
            query_builder: Optional query builder for efficient queries
        """
        self.session = session
        self.workspace_id = workspace_id
        self.query_builder = query_builder or EntityQueryBuilder(session)
        self.field_validators = FieldValidators(session, workspace_id)
        self.record_validators = RecordValidators(session, workspace_id)

    async def validate_and_plan_updates(
        self,
        record_id: UUID,
        updates: dict[str, Any],
        depth: int = 0,
    ) -> UpdatePlan:
        """Validate nested updates and build execution plan.

        This method:
        1. Validates all updates before execution
        2. Builds a complete update plan
        3. Detects circular references
        4. Validates JSON depth limits
        5. Ensures all target entities exist and are accessible

        Args:
            record_id: Record to update
            updates: Updates including nested relation updates
            depth: Current nesting depth

        Returns:
            UpdatePlan with all update steps

        Raises:
            TracecatValidationError: If validation fails
        """
        plan = UpdatePlan()
        await self._build_update_plan(plan, record_id, updates, depth)

        # Validate the complete plan
        if len(plan.steps) > self.MAX_UPDATE_SIZE:
            raise TracecatValidationError(
                f"Update plan exceeds maximum size of {self.MAX_UPDATE_SIZE} operations"
            )

        return plan

    async def _build_update_plan(
        self,
        plan: UpdatePlan,
        record_id: UUID,
        updates: dict[str, Any],
        depth: int,
    ) -> None:
        """Recursively build the update plan.

        Args:
            plan: Update plan being built
            record_id: Current record ID
            updates: Updates for this record
            depth: Current depth

        Raises:
            TracecatValidationError: On validation errors
        """
        # Check depth limit
        if depth > self.MAX_RELATION_DEPTH:
            raise TracecatValidationError(
                f"Maximum relation depth {self.MAX_RELATION_DEPTH} exceeded"
            )

        # Check for circular reference
        if record_id in plan.visited_records:
            logger.warning(f"Circular reference detected for record {record_id}")
            return  # Skip this update to avoid infinite loop

        plan.visited_records.add(record_id)

        # Load record and fields
        record = await self.record_validators.validate_record_exists(record_id)
        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")

        # Load active relations keyed by source_key
        rel_stmt = select(RelationDefinition).where(
            RelationDefinition.source_entity_id == record.entity_id,
            RelationDefinition.is_active,
            RelationDefinition.owner_id == self.workspace_id,
        )
        rels = list((await self.session.exec(rel_stmt)).all())
        rel_map = {r.source_key: r for r in rels}

        # Separate regular and relation updates
        regular_updates = {}
        relation_updates = {}

        for key, value in updates.items():
            if key in rel_map:
                rel_def = rel_map[key]
                rt = RelationType(rel_def.relation_type)
                if (
                    rt in (RelationType.ONE_TO_ONE, RelationType.MANY_TO_ONE)
                    and isinstance(value, dict)
                    and value
                ):
                    relation_updates[key] = (rel_def, value)
                elif isinstance(value, dict) and not value:
                    continue
                else:
                    # Not a nested relation update
                    pass
            else:
                # Regular field update - validate JSON depth
                if not self._validate_json_depth(value):
                    raise TracecatValidationError(
                        f"Field {key} exceeds maximum JSON depth of {self.MAX_JSON_DEPTH}"
                    )
                regular_updates[key] = value

        # Add this update step to the plan
        if regular_updates or depth == 0:  # Always add root update
            step = UpdateStep(
                record_id=record_id,
                entity_id=record.entity_id,
                field_updates=regular_updates,
                depth=depth,
            )
            plan.steps.append(step)

        # Process nested relation updates
        for _key, (rel_def, nested_value) in relation_updates.items():
            # Find the linked record
            link = await self._get_relation_link_by_rel(record_id, rel_def.id)
            if link:
                # Store the link for reference
                plan.relation_links[(record_id, rel_def.id)] = link.target_record_id
                # Recursively build plan for nested update
                await self._build_update_plan(
                    plan, link.target_record_id, nested_value, depth + 1
                )

    async def _get_relation_link_by_rel(
        self, source_record_id: UUID, relation_definition_id: UUID
    ) -> RecordRelationLink | None:
        """Get relation link for a record and field.

        Args:
            source_record_id: Source record ID
            relation_definition_id: Relation definition ID

        Returns:
            RecordRelationLink if found, None otherwise
        """
        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == source_record_id,
            RecordRelationLink.relation_definition_id == relation_definition_id,
        )
        result = await self.session.exec(stmt)
        return result.first()

    def _validate_json_depth(self, obj: Any, depth: int = 0) -> bool:
        """Validate JSON structure depth doesn't exceed limit.

        Args:
            obj: Object to validate
            depth: Current depth

        Returns:
            True if within depth limit, False otherwise
        """
        if depth > self.MAX_JSON_DEPTH:
            return False

        if isinstance(obj, dict):
            return all(self._validate_json_depth(v, depth + 1) for v in obj.values())
        elif isinstance(obj, list):
            return all(self._validate_json_depth(item, depth + 1) for item in obj)

        return True

    async def batch_load_relation_links(
        self, record_ids: list[UUID]
    ) -> dict[UUID, list[RecordRelationLink]]:
        """Batch load relation links for multiple records.

        Performance optimization to avoid N+1 queries.

        Args:
            record_ids: List of record IDs

        Returns:
            Dict mapping record ID to list of relation links
        """
        from collections import defaultdict
        from typing import cast

        from sqlalchemy.sql import ColumnElement

        source_record_col = cast(
            ColumnElement[Any], RecordRelationLink.source_record_id
        )
        stmt = select(RecordRelationLink).where(
            source_record_col.in_(record_ids),
            RecordRelationLink.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)

        links_by_record: dict[UUID, list[RecordRelationLink]] = defaultdict(list)
        for link in result:
            links_by_record[link.source_record_id].append(link)

        return dict(links_by_record)


# Previous RelationValidators removed.
