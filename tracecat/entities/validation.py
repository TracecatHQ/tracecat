"""Validators for custom entities.

This module provides both pure validation functions and database-dependent validators.
Pure functions are at module level, database-dependent validators are in classes.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic_core import PydanticCustomError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    Entity,
    FieldMetadata,
    Record,
    RecordRelationLink,
)
from tracecat.entities.enums import RelationKind
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import (
    FieldType,
    validate_field_value_type,
    validate_flat_structure,
)
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError


# Relation Nesting Configuration
class RelationNestingPolicy(Enum):
    """Policy for relation nesting restrictions.

    This enum allows easy configuration of relation restrictions.
    Future policies can be added without changing validation logic.
    """

    BLOCK_ALL = "block_all"  # No relations to entities with relations
    ALLOW_ONE_LEVEL = "allow_one_level"  # Allow 1 level of nesting
    ALLOW_TWO_LEVELS = "allow_two_levels"  # Allow 2 levels
    UNRESTRICTED = "unrestricted"  # No restrictions


# Module-level configuration (easy to change in the future)
CURRENT_NESTING_POLICY = RelationNestingPolicy.BLOCK_ALL


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
        FieldType.RELATION_ONE_TO_ONE,
        FieldType.RELATION_ONE_TO_MANY,
        FieldType.RELATION_MANY_TO_ONE,
        FieldType.RELATION_MANY_TO_MANY,
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
            .join(Entity, FieldMetadata.entity_id == Entity.id)  # type: ignore[arg-type]
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
            .join(Entity, FieldMetadata.entity_id == Entity.id)  # type: ignore[arg-type]
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

        # Check for acceptable structure - only for active fields
        for key, value in data.items():
            if key in active_fields and not validate_flat_structure(value):
                errors.append(
                    f"Field '{key}': Nested arrays or excessive nesting (>3 levels) not allowed"
                )

        if errors:
            raise TracecatValidationError("; ".join(errors))

        for key, value in data.items():
            if key not in active_fields:
                # Silently skip inactive/unknown fields
                continue

            field = active_fields[key]

            # Handle relation fields separately
            if field.relation_kind:
                if value is not None:
                    # Validate relation field value
                    is_valid, error = await self._validate_relation_field_value(
                        field, value
                    )
                    if not is_valid:
                        errors.append(f"Field '{key}': {error}")
                        continue
                validated[key] = value
            else:
                # Regular field validation
                if value is not None:
                    # Validate the value against the field type
                    try:
                        validate_field_value_type(
                            value, FieldType(field.field_type), field.enum_options
                        )
                    except PydanticCustomError as e:
                        errors.append(f"Field '{key}': {e.message()}")
                        continue
                validated[key] = value

        if errors:
            raise TracecatValidationError("; ".join(errors))

        return validated

    async def _validate_relation_field_value(
        self, field: FieldMetadata, value: Any
    ) -> tuple[bool, str | None]:
        """Validate a relation field value.

        Args:
            field: Field metadata with relation info
            value: Value to validate

        Returns:
            Tuple of (is_valid, error_message)
        """

        if field.relation_kind == RelationKind.ONE_TO_ONE:
            # ONE_TO_ONE expects a single UUID, string UUID, or dict for inline creation
            if isinstance(value, dict):
                # Allow dict for inline record creation
                # Basic validation - ensure it's not empty
                if not value:
                    return False, "Empty dict not allowed for relation field"
                return True, None
            try:
                validate_relation_uuid(
                    value, allow_none=False, context="one_to_one relation"
                )
                return True, None
            except PydanticCustomError as e:
                return False, e.message()

        elif field.relation_kind == RelationKind.ONE_TO_MANY:
            # ONE_TO_MANY expects a list of UUIDs or dicts
            if not isinstance(value, list):
                return (
                    False,
                    f"Expected list for one_to_many relation, got {type(value).__name__}",
                )

            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    # Allow dict for inline record creation
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

        return False, f"Unknown relation kind: {field.relation_kind}"


class RelationNestingValidator:
    """Validator for relation nesting restrictions.

    This validator checks if creating a relation field would violate
    the configured nesting policy. Easy to extend for future policies.
    """

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
        policy: RelationNestingPolicy = CURRENT_NESTING_POLICY,
    ):
        """Initialize nesting validator.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
            policy: Nesting policy to enforce (defaults to module config)
        """
        self.session = session
        self.workspace_id = workspace_id
        self.policy = policy

    async def validate_relation_creation(
        self,
        source_entity_id: UUID,
        target_entity_id: UUID,
    ) -> tuple[bool, str | None]:
        """Validate if a relation can be created based on nesting policy.

        Args:
            source_entity_id: Entity where the relation field is being created
            target_entity_id: Entity that the relation will point to

        Returns:
            Tuple of (is_valid, error_message)
        """
        if self.policy == RelationNestingPolicy.UNRESTRICTED:
            return True, None

        if self.policy == RelationNestingPolicy.BLOCK_ALL:
            # Check if target entity has any relation fields
            if await self._entity_has_relations(target_entity_id):
                return False, (
                    "Cannot create relation to an entity that has relation fields. "
                    "Nested relations are currently not supported."
                )

            # Check if any entity already references the source entity
            if await self._entity_is_referenced(source_entity_id):
                return False, (
                    "Cannot create relation field on an entity that is already "
                    "referenced by another entity. Nested relations are currently not supported."
                )

        # Future policies can be implemented here
        elif self.policy == RelationNestingPolicy.ALLOW_ONE_LEVEL:
            # TODO: Implement 1-level nesting validation
            # This would check the depth of the relation chain
            pass
        elif self.policy == RelationNestingPolicy.ALLOW_TWO_LEVELS:
            # TODO: Implement 2-level nesting validation
            pass

        return True, None

    async def _entity_has_relations(self, entity_id: UUID) -> bool:
        """Check if entity has any active relation fields.

        Args:
            entity_id: Entity to check

        Returns:
            True if entity has relation fields, False otherwise
        """
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_id == entity_id,
            col(FieldMetadata.relation_kind).isnot(None),
            FieldMetadata.is_active,
        )
        result = await self.session.exec(stmt)
        return result.first() is not None

    async def _entity_is_referenced(self, entity_id: UUID) -> bool:
        """Check if any other entity has a relation field targeting this entity.

        Args:
            entity_id: Entity to check

        Returns:
            True if entity is referenced by another entity, False otherwise
        """
        stmt = select(FieldMetadata).where(
            FieldMetadata.target_entity_id == entity_id,
            FieldMetadata.is_active,
        )
        result = await self.session.exec(stmt)
        return result.first() is not None


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

        # Load entity fields
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_id == record.entity_id,
            FieldMetadata.is_active,
        )
        result = await self.session.exec(stmt)
        fields = list(result.all())

        # Separate regular and relation updates
        regular_updates = {}
        relation_updates = {}

        for key, value in updates.items():
            field = next((f for f in fields if f.field_key == key), None)
            if not field:
                logger.debug(f"Skipping unknown field {key}")
                continue

            # Check if it's a relation field with nested updates
            if (
                field.relation_kind == RelationKind.ONE_TO_ONE
                and isinstance(value, dict)
                and value  # Non-empty dict
            ):
                # This is a nested relation update
                relation_updates[key] = (field, value)
            elif field.relation_kind and isinstance(value, dict) and not value:
                # Empty dict for relation field - skip it entirely
                continue
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
        for _key, (field, nested_value) in relation_updates.items():
            # Find the linked record
            link = await self._get_relation_link(record_id, field.id)
            if link:
                # Store the link for reference
                plan.relation_links[(record_id, field.id)] = link.target_record_id
                # Recursively build plan for nested update
                await self._build_update_plan(
                    plan, link.target_record_id, nested_value, depth + 1
                )

    async def _get_relation_link(
        self, source_record_id: UUID, source_field_id: UUID
    ) -> RecordRelationLink | None:
        """Get relation link for a record and field.

        Args:
            source_record_id: Source record ID
            source_field_id: Source field ID

        Returns:
            RecordRelationLink if found, None otherwise
        """
        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == source_record_id,
            RecordRelationLink.source_field_id == source_field_id,
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

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id.in_(record_ids),
            RecordRelationLink.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)

        links_by_record: dict[UUID, list[RecordRelationLink]] = defaultdict(list)
        for link in result:
            links_by_record[link.source_record_id].append(link)

        return dict(links_by_record)


class RelationValidators:
    """Database-dependent validators for relation operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        """Initialize relation validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id
        self.entity_validators = EntityValidators(session, workspace_id)
        self.record_validators = RecordValidators(session, workspace_id)
        self.nesting_validator = RelationNestingValidator(session, workspace_id)

    # Removed unused validate_target_entity / validate_target_record helpers (dead code)
