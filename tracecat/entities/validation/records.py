"""Record-level validators and nested update planning.

Includes:
- RecordValidators: existence checks and per-field value validation using
  FieldType rules; relation value shape validation for inline creation/links.
- NestedUpdateValidator: plans nested updates across existing relation links
  (e.g., update fields on a related record via a dict payload), enforcing a
  maximum traversal depth and preventing cycles. Relation links themselves are
  not modified here; only regular field values on target records are updated.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from pydantic_core import PydanticCustomError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    FieldMetadata,
    Record,
    RecordRelationLink,
    RelationDefinition,
)
from tracecat.entities.enums import RelationType
from tracecat.entities.types import FieldType, validate_field_value_type
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError


def validate_relation_uuid(
    value: Any, allow_none: bool = False, context: str = "relation"
) -> UUID | None:
    """Validate and convert a value to UUID for relations.

    Accepts UUID or string; allows None when explicitly enabled.
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


class RecordValidators:
    """Database-dependent validators for record-level operations."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
    ):
        self.session = session
        self.workspace_id = workspace_id

    async def validate_record_exists(
        self,
        record_id: UUID,
        entity_id: UUID | None = None,
        raise_on_missing: bool = True,
    ) -> Record | None:
        """Validate that a record exists in this workspace (and entity if given)."""
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
        """Validate record data against field definitions and relation shapes.

        Regular fields: enforced via FieldType validators (type, JSON rules).
        Relation fields: allow UUID(s) or inline dict(s) based on relation type.
        Unknown keys are ignored.
        """
        errors: list[str] = []
        validated: dict[str, Any] = {}

        active_fields = {f.field_key: f for f in fields if f.is_active}
        relations = relations or {}

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
        """Validate a relation field value shape for a given relation type."""
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


@dataclass
class UpdateStep:
    """Single update operation in the nested update plan."""

    record_id: UUID
    entity_id: UUID
    field_updates: dict[str, Any]  # Regular field updates
    depth: int  # Nesting depth for this update


@dataclass
class UpdatePlan:
    """Execution plan for nested updates across relation links."""

    steps: list[UpdateStep] = field(default_factory=list)
    visited_records: set[UUID] = field(default_factory=set)
    # (source_record_id, relation_definition_id) -> target_record_id
    relation_links: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)


class NestedUpdateValidator:
    """Validator and planner for nested relation updates during record updates.

    Example: updating a record's "manager" relation with a dict payload will
    plan an update for the related manager record's regular fields.
    """

    # Configuration limits
    MAX_RELATION_DEPTH = 2  # Maximum depth for nested relation updates
    MAX_UPDATE_SIZE = 100  # Maximum number of fields in single update

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
    ):
        self.session = session
        self.workspace_id = workspace_id
        # Lazy import to avoid cycles
        from .fields import FieldValidators  # noqa: WPS433

        self.field_validators = FieldValidators(session, workspace_id)
        self.record_validators = RecordValidators(session, workspace_id)

    async def validate_and_plan_updates(
        self,
        record_id: UUID,
        updates: dict[str, Any],
        depth: int = 0,
    ) -> UpdatePlan:
        """Validate nested updates and build execution plan.

        - Validates all updates before execution
        - Builds a complete update plan
        - Detects circular references
        - Ensures all target entities exist and are accessible
        """
        plan = UpdatePlan()
        await self._build_update_plan(plan, record_id, updates, depth)

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
        """Recursively build the update plan for the record and its relations."""
        if depth > self.MAX_RELATION_DEPTH:
            raise TracecatValidationError(
                f"Maximum relation depth {self.MAX_RELATION_DEPTH} exceeded"
            )

        if record_id in plan.visited_records:
            logger.warning(f"Circular reference detected for record {record_id}")
            return
        plan.visited_records.add(record_id)

        # Load record and relations for this entity
        record = cast(
            Record, await self.record_validators.validate_record_exists(record_id)
        )

        rel_stmt = select(RelationDefinition).where(
            RelationDefinition.source_entity_id == record.entity_id,
            RelationDefinition.is_active,
            RelationDefinition.owner_id == self.workspace_id,
        )
        rels = list((await self.session.exec(rel_stmt)).all())
        rel_map = {r.source_key: r for r in rels}

        # Separate regular and relation updates
        regular_updates: dict[str, Any] = {}
        relation_updates: dict[str, tuple[RelationDefinition, dict[str, Any]]] = {}

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
                    continue  # Not a nested relation update
            else:
                regular_updates[key] = value

        # Always add root update; add others if they have regular updates
        if regular_updates or depth == 0:
            plan.steps.append(
                UpdateStep(
                    record_id=record_id,
                    entity_id=record.entity_id,
                    field_updates=regular_updates,
                    depth=depth,
                )
            )

        # Process nested relation updates
        for _key, (rel_def, nested_value) in relation_updates.items():
            link = await self._get_relation_link_by_rel(record_id, rel_def.id)
            if link:
                plan.relation_links[(record_id, rel_def.id)] = link.target_record_id
                await self._build_update_plan(
                    plan, link.target_record_id, nested_value, depth + 1
                )

    async def _get_relation_link_by_rel(
        self, source_record_id: UUID, relation_definition_id: UUID
    ) -> RecordRelationLink | None:
        """Get relation link for a record and relation definition."""
        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == source_record_id,
            RecordRelationLink.relation_definition_id == relation_definition_id,
        )
        result = await self.session.exec(stmt)
        return result.first()
