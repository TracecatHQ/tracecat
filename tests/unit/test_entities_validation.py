"""Tests for entity validation module.

Includes tests for entity/field/record validators and nested update validator.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    Entity,
    FieldMetadata,
    Record,
    RecordRelationLink,
    RelationDefinition,
    Workspace,
)
from tracecat.entities.models import FieldMetadataCreate
from tracecat.entities.types import FieldType
from tracecat.entities.validation import (
    EntityValidators,
    FieldValidators,
    NestedUpdateValidator,
    RecordValidators,
)
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def entity_validators(session: AsyncSession, svc_role: Role) -> EntityValidators:
    """Create EntityValidators instance."""
    return EntityValidators(session=session, workspace_id=svc_role.workspace_id)


@pytest.fixture
async def field_validators(session: AsyncSession, svc_role: Role) -> FieldValidators:
    """Create FieldValidators instance."""
    return FieldValidators(session=session, workspace_id=svc_role.workspace_id)


@pytest.fixture
async def record_validators(session: AsyncSession, svc_role: Role) -> RecordValidators:
    """Create RecordValidators instance."""
    return RecordValidators(session=session, workspace_id=svc_role.workspace_id)


# RelationValidators removed in new model


# ConstraintValidators fixture removed - class no longer exists


@pytest.fixture
async def test_entity(session: AsyncSession, svc_workspace: Workspace) -> Entity:
    """Create a test entity."""
    entity = Entity(
        name="test_entity",
        display_name="Test Entity",
        description="Test entity for validation",
        owner_id=svc_workspace.id,
        is_active=True,
    )
    session.add(entity)
    await session.commit()
    await session.refresh(entity)
    return entity


@pytest.fixture
async def test_field(
    session: AsyncSession, test_entity: Entity, svc_workspace: Workspace
) -> FieldMetadata:
    """Create a test field."""
    field = FieldMetadata(
        entity_id=test_entity.id,
        field_key="test_field",
        field_type=FieldType.TEXT.value,
        display_name="Test Field",
        is_active=True,
    )
    session.add(field)
    await session.commit()
    await session.refresh(field)
    return field


@pytest.fixture
async def test_record(
    session: AsyncSession,
    test_entity: Entity,
    svc_workspace: Workspace,
) -> Record:
    """Create a test record."""
    record = Record(
        entity_id=test_entity.id,
        field_data={"test_field": "test_value"},
        owner_id=svc_workspace.id,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@pytest.mark.anyio
class TestEntityValidators:
    """Test entity-level validations."""

    async def test_validate_entity_exists(
        self, entity_validators: EntityValidators, test_entity: Entity
    ):
        """Test validating entity existence."""
        # Should find existing entity
        result = await entity_validators.validate_entity_exists(test_entity.id)
        assert result is not None
        assert result.id == test_entity.id

        # Should raise for non-existent entity
        fake_id = UUID("00000000-0000-0000-0000-000000000000")
        with pytest.raises(TracecatNotFoundError):
            await entity_validators.validate_entity_exists(fake_id)

        # Should return None when not raising
        result = await entity_validators.validate_entity_exists(
            fake_id, raise_on_missing=False
        )
        assert result is None

    async def test_validate_entity_name_unique(
        self,
        entity_validators: EntityValidators,
        test_entity: Entity,
    ):
        """Test validating entity name uniqueness."""
        # Should pass for unique name
        await entity_validators.validate_entity_name_unique("unique_name")

        # Should raise for duplicate name
        with pytest.raises(PydanticCustomError) as exc_info:
            await entity_validators.validate_entity_name_unique(test_entity.name)
        assert "already exists" in str(exc_info.value)

        # Should pass when excluding current entity
        await entity_validators.validate_entity_name_unique(
            test_entity.name, exclude_id=test_entity.id
        )

    async def test_validate_entity_active(
        self,
        entity_validators: EntityValidators,
        test_entity: Entity,
        session: AsyncSession,
    ):
        """Test validating entity active status."""
        # Should pass for active entity
        result = await entity_validators.validate_entity_active(test_entity.id)
        assert result.is_active

        # Deactivate entity
        test_entity.is_active = False
        session.add(test_entity)
        await session.commit()

        # Should raise for inactive entity
        with pytest.raises(TracecatValidationError) as exc_info:
            await entity_validators.validate_entity_active(test_entity.id)
        assert "not active" in str(exc_info.value)


@pytest.mark.anyio
class TestFieldValidators:
    """Test field-level validations."""

    async def test_validate_field_exists(
        self, field_validators: FieldValidators, test_field: FieldMetadata
    ):
        """Test validating field existence."""
        # Should find existing field
        result = await field_validators.validate_field_exists(test_field.id)
        assert result is not None
        assert result.id == test_field.id

        # Should raise for non-existent field
        fake_id = UUID("00000000-0000-0000-0000-000000000000")
        with pytest.raises(TracecatNotFoundError):
            await field_validators.validate_field_exists(fake_id)

        # Should return None when not raising
        result = await field_validators.validate_field_exists(
            fake_id, raise_on_missing=False
        )
        assert result is None

    async def test_validate_field_key_unique(
        self,
        field_validators: FieldValidators,
        test_entity: Entity,
        test_field: FieldMetadata,
    ):
        """Test validating field key uniqueness."""
        # Should pass for unique key
        await field_validators.validate_field_key_unique(test_entity.id, "unique_key")

        # Should raise for duplicate key
        with pytest.raises(PydanticCustomError) as exc_info:
            await field_validators.validate_field_key_unique(
                test_entity.id, test_field.field_key
            )
        assert "already exists" in str(exc_info.value)

        # Should pass when excluding current field
        await field_validators.validate_field_key_unique(
            test_entity.id, test_field.field_key, exclude_id=test_field.id
        )

    async def test_validate_enum_options_for_select_fields(self):
        """Test enum options validation for SELECT fields using Pydantic model."""

        # Should pass for SELECT with options
        field = FieldMetadataCreate(
            field_key="select_field",
            field_type=FieldType.SELECT,
            display_name="Select Field",
            enum_options=["option1", "option2"],
        )
        assert field.enum_options == ["option1", "option2"]

        # Should pass for MULTI_SELECT with options
        field = FieldMetadataCreate(
            field_key="multi_select_field",
            field_type=FieldType.MULTI_SELECT,
            display_name="Multi Select Field",
            enum_options=["option1", "option2"],
        )
        assert field.enum_options == ["option1", "option2"]

        # Should raise for SELECT without options
        with pytest.raises(ValidationError) as exc_info:
            FieldMetadataCreate(
                field_key="select_field",
                field_type=FieldType.SELECT,
                display_name="Select Field",
                enum_options=None,
            )
        assert "requires enum_options" in str(exc_info.value)

    async def test_validate_enum_options_rejects_non_select_fields(self):
        """Test enum options validation rejects non-SELECT fields using Pydantic model."""

        # Should raise for non-SELECT field with options
        with pytest.raises(ValidationError) as exc_info:
            FieldMetadataCreate(
                field_key="text_field",
                field_type=FieldType.TEXT,
                display_name="Text Field",
                enum_options=["option1"],
            )
        assert "cannot have enum_options" in str(exc_info.value)

        # Should pass for non-SELECT field without options
        field = FieldMetadataCreate(
            field_key="text_field",
            field_type=FieldType.TEXT,
            display_name="Text Field",
            enum_options=None,
        )
        assert field.enum_options is None

    async def test_validate_field_type_supports_default(self):
        """Test validation of field types that support default values using Pydantic model."""

        # Should pass for primitive types
        field = FieldMetadataCreate(
            field_key="text_field",
            field_type=FieldType.TEXT,
            display_name="Text Field",
            default_value="default text",
        )
        assert field.default_value == "default text"

        field = FieldMetadataCreate(
            field_key="number_field",
            field_type=FieldType.NUMBER,
            display_name="Number Field",
            default_value=42.5,
        )
        assert field.default_value == 42.5

        field = FieldMetadataCreate(
            field_key="bool_field",
            field_type=FieldType.BOOL,
            display_name="Bool Field",
            default_value=True,
        )
        assert field.default_value is True


@pytest.mark.anyio
class TestRecordValidators:
    """Test record-level validations."""

    async def test_validate_record_exists(
        self,
        record_validators: RecordValidators,
        test_record: Record,
        test_entity: Entity,
    ):
        """Test validating record existence."""
        # Should find existing record
        result = await record_validators.validate_record_exists(test_record.id)
        assert result is not None
        assert result.id == test_record.id

        # Should verify entity association
        result = await record_validators.validate_record_exists(
            test_record.id, test_entity.id
        )
        assert result is not None

        # Should raise for wrong entity
        fake_entity_id = UUID("00000000-0000-0000-0000-000000000000")
        with pytest.raises(TracecatNotFoundError):
            await record_validators.validate_record_exists(
                test_record.id, fake_entity_id
            )

    # test_validate_required_fields removed - is_required constraint no longer exists

    async def test_validate_record_data_type_checking(
        self,
        record_validators: RecordValidators,
        test_entity: Entity,
        test_field: FieldMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test record data type validation."""
        # Create number field
        number_field = FieldMetadata(
            entity_id=test_entity.id,
            field_key="number_field",
            field_type=FieldType.NUMBER.value,
            display_name="Number Field",
            is_active=True,
        )
        session.add(number_field)
        await session.commit()

        # Should validate correct types
        validated = await record_validators.validate_record_data(
            {"test_field": "text", "number_field": 42},
            [test_field, number_field],
        )
        assert validated["test_field"] == "text"
        assert validated["number_field"] == 42

        # Should raise for wrong type
        with pytest.raises(TracecatValidationError) as exc_info:
            await record_validators.validate_record_data(
                {"number_field": "not a number"},
                [number_field],
            )
        assert "number_field" in str(exc_info.value)

    async def test_validate_record_data_structure_limits(
        self,
        record_validators: RecordValidators,
        test_field: FieldMetadata,
        session: AsyncSession,
    ):
        """Test that nested structures have appropriate limits."""
        # TEXT fields still expect strings only
        validated = await record_validators.validate_record_data(
            {"test_field": "plain text value"},
            [test_field],
        )
        assert validated["test_field"] == "plain text value"

        # TEXT fields reject objects
        with pytest.raises(TracecatValidationError) as exc_info:
            await record_validators.validate_record_data(
                {"test_field": {"nested": "object"}},
                [test_field],
            )
        assert "Expected string" in str(exc_info.value)

        # For testing nested structure validation, we check at data level
        # Arrays with nested arrays should be rejected
        from tracecat.entities.types import validate_flat_structure

        # Simple nested object - should pass
        assert validate_flat_structure({"level1": {"level2": "value"}}) is True

        # 3 levels of nesting - should pass
        assert validate_flat_structure({"l1": {"l2": {"l3": "value"}}}) is True

        # >3 levels of nesting - should fail
        assert (
            validate_flat_structure({"l1": {"l2": {"l3": {"l4": "too deep"}}}}) is False
        )

        # Nested arrays - should fail
        assert validate_flat_structure([["nested", "array"]]) is False


@pytest.mark.anyio
class TestRelationValueValidation:
    """Test relation value shape validation via RecordValidators."""

    async def test_relation_value_shapes(self, record_validators: RecordValidators):
        # Build a fake relation definition mapping
        from unittest.mock import MagicMock

        def make_fake_rel(relation_type: str):
            mock = MagicMock()
            mock.relation_type = relation_type
            return mock

        rels = {
            "owner": make_fake_rel("one_to_one"),
            "members": make_fake_rel("one_to_many"),
        }

        # ONE_TO_ONE accepts UUID string or dict
        validated = await record_validators.validate_record_data(
            {"owner": "00000000-0000-0000-0000-000000000001"}, [], rels
        )
        assert "owner" in validated

        validated = await record_validators.validate_record_data(
            {"owner": {"name": "Alice"}}, [], rels
        )
        assert validated["owner"]["name"] == "Alice"

        # ONE_TO_MANY accepts list of UUIDs or dicts
        validated = await record_validators.validate_record_data(
            {"members": ["00000000-0000-0000-0000-000000000002"]}, [], rels
        )
        assert "members" in validated


# === Nested Update Validator Tests (consolidated) === #


@pytest.fixture
async def entity_with_relations(session: AsyncSession, svc_workspace) -> Entity:
    """Create an entity with a relation definition and a primitive field."""
    entity = Entity(
        id=uuid4(),
        name="test_entity",
        display_name="Test Entity",
        owner_id=svc_workspace.id,
        is_active=True,
    )
    session.add(entity)

    # Add regular field
    text_field = FieldMetadata(
        id=uuid4(),
        entity_id=entity.id,
        field_key="text_col",
        field_type=FieldType.TEXT,
        display_name="Text Column",
        is_active=True,
    )
    session.add(text_field)

    # Add relation definition (self-referential)
    rel_def = RelationDefinition(
        id=uuid4(),
        owner_id=svc_workspace.id,
        source_entity_id=entity.id,
        target_entity_id=entity.id,
        source_key="manager",
        display_name="Manager",
        relation_type="one_to_one",
        is_active=True,
    )
    session.add(rel_def)

    await session.commit()
    return entity


@pytest.fixture
async def linked_records(
    session: AsyncSession, entity_with_relations: Entity, svc_workspace
) -> tuple[Record, Record, RecordRelationLink]:
    """Create two linked records using the relation definition."""
    # Get the relation definition
    stmt = select(RelationDefinition).where(
        RelationDefinition.source_entity_id == entity_with_relations.id,
        RelationDefinition.source_key == "manager",
    )
    relation_def = (await session.exec(stmt)).first()
    assert relation_def is not None, "Relation definition not found"

    # Create main record
    main_record = Record(
        id=uuid4(),
        entity_id=entity_with_relations.id,
        field_data={"text_col": "main"},
        owner_id=svc_workspace.id,
    )
    session.add(main_record)

    # Create target record
    target_record = Record(
        id=uuid4(),
        entity_id=entity_with_relations.id,
        field_data={"text_col": "target"},
        owner_id=svc_workspace.id,
    )
    session.add(target_record)

    # Create relation link
    link = RecordRelationLink(
        id=uuid4(),
        source_record_id=main_record.id,
        relation_definition_id=relation_def.id,
        target_record_id=target_record.id,
        source_entity_id=entity_with_relations.id,
        target_entity_id=entity_with_relations.id,
        owner_id=svc_workspace.id,
    )
    session.add(link)

    await session.commit()
    return main_record, target_record, link


@pytest.mark.anyio
class TestNestedUpdateValidator:
    """Tests for NestedUpdateValidator functionality."""

    async def test_validate_simple_update_no_relations(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        # Create a record
        record = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={"text_col": "original"},
            owner_id=svc_workspace.id,
        )
        session.add(record)
        await session.commit()

        # Create validator
        validator = NestedUpdateValidator(session, svc_workspace.id)

        # Plan simple update
        updates = {"text_col": "updated"}
        plan = await validator.validate_and_plan_updates(record.id, updates)

        # Verify plan
        assert len(plan.steps) == 1
        assert plan.steps[0].record_id == record.id
        assert plan.steps[0].field_updates == {"text_col": "updated"}
        assert plan.steps[0].depth == 0

    async def test_validate_nested_relation_update(
        self,
        session: AsyncSession,
        linked_records: tuple[Record, Record, RecordRelationLink],
        svc_workspace,
    ):
        main_record, target_record, link = linked_records

        validator = NestedUpdateValidator(session, svc_workspace.id)
        updates = {
            "text_col": "main_updated",
            "manager": {"text_col": "target_updated"},
        }
        plan = await validator.validate_and_plan_updates(main_record.id, updates)

        assert len(plan.steps) == 2
        main_step = next(s for s in plan.steps if s.record_id == main_record.id)
        assert main_step.field_updates == {"text_col": "main_updated"}
        assert main_step.depth == 0

        target_step = next(s for s in plan.steps if s.record_id == target_record.id)
        assert target_step.field_updates == {"text_col": "target_updated"}
        assert target_step.depth == 1

        # Relation link tracking by relation_definition_id
        assert (main_record.id, link.relation_definition_id) in plan.relation_links
        assert (
            plan.relation_links[(main_record.id, link.relation_definition_id)]
            == target_record.id
        )

    async def test_max_depth_exceeded(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        # Create chain of linked records
        records = []
        for i in range(4):
            record = Record(
                id=uuid4(),
                entity_id=entity_with_relations.id,
                field_data={"text_col": f"record_{i}"},
                owner_id=svc_workspace.id,
            )
            session.add(record)
            records.append(record)

        # Get relation definition
        stmt = select(RelationDefinition).where(
            RelationDefinition.source_entity_id == entity_with_relations.id,
            RelationDefinition.source_key == "manager",
        )
        relation_def = (await session.exec(stmt)).first()
        assert relation_def is not None, "Relation definition not found"

        # Create chain of links
        for i in range(3):
            link = RecordRelationLink(
                id=uuid4(),
                source_record_id=records[i].id,
                relation_definition_id=relation_def.id,
                target_record_id=records[i + 1].id,
                source_entity_id=entity_with_relations.id,
                target_entity_id=entity_with_relations.id,
                owner_id=svc_workspace.id,
            )
            session.add(link)

        await session.commit()

        validator = NestedUpdateValidator(session, svc_workspace.id)
        updates = {
            "text_col": "level_0",
            "manager": {
                "text_col": "level_1",
                "manager": {
                    "text_col": "level_2",
                    "manager": {"text_col": "level_3"},
                },
            },
        }

        with pytest.raises(TracecatValidationError) as exc_info:
            await validator.validate_and_plan_updates(records[0].id, updates)
        assert "Maximum relation depth" in str(exc_info.value)

    async def test_circular_reference_detection(
        self, session: AsyncSession, entity_with_relations: Entity, svc_workspace
    ):
        # Create two records that reference each other
        record1 = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={"text_col": "record1"},
            owner_id=svc_workspace.id,
        )
        record2 = Record(
            id=uuid4(),
            entity_id=entity_with_relations.id,
            field_data={"text_col": "record2"},
            owner_id=svc_workspace.id,
        )
        session.add(record1)
        session.add(record2)

        # Get relation definition
        stmt = select(RelationDefinition).where(
            RelationDefinition.source_entity_id == entity_with_relations.id,
            RelationDefinition.source_key == "manager",
        )
        relation_def = (await session.exec(stmt)).first()
        assert relation_def is not None, "Relation definition not found"

        # Create circular links
        session.add(
            RecordRelationLink(
                id=uuid4(),
                source_record_id=record1.id,
                relation_definition_id=relation_def.id,
                target_record_id=record2.id,
                source_entity_id=entity_with_relations.id,
                target_entity_id=entity_with_relations.id,
                owner_id=svc_workspace.id,
            )
        )
        session.add(
            RecordRelationLink(
                id=uuid4(),
                source_record_id=record2.id,
                relation_definition_id=relation_def.id,
                target_record_id=record1.id,
                source_entity_id=entity_with_relations.id,
                target_entity_id=entity_with_relations.id,
                owner_id=svc_workspace.id,
            )
        )
        await session.commit()

        validator = NestedUpdateValidator(session, svc_workspace.id)
        updates = {"manager": {"text_col": "updated"}}
        plan = await validator.validate_and_plan_updates(record1.id, updates)

        # Should handle circular reference gracefully by skipping infinite loop
        assert len(plan.steps) >= 1

    async def test_validate_target_record_exists_and_matches_entity(
        self,
        record_validators: RecordValidators,
        test_record: Record,
        test_entity: Entity,
    ):
        """Test target record validation using record_validators."""
        # Should find existing record in correct entity using record_validators
        result = await record_validators.validate_record_exists(
            test_record.id, test_entity.id
        )
        assert result is not None
        assert result.id == test_record.id

        # Should raise for wrong entity
        fake_entity_id = UUID("00000000-0000-0000-0000-000000000000")
        with pytest.raises(TracecatNotFoundError):
            await record_validators.validate_record_exists(
                test_record.id, fake_entity_id
            )
