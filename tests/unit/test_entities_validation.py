"""Tests for entity validation module."""

from uuid import UUID

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    Entity,
    FieldMetadata,
    Record,
    Workspace,
)
from tracecat.entities.enums import RelationType
from tracecat.entities.models import FieldMetadataCreate, RelationSettings
from tracecat.entities.types import FieldType
from tracecat.entities.validation import (
    EntityValidators,
    FieldValidators,
    RecordValidators,
    RelationValidators,
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


@pytest.fixture
async def relation_validators(
    session: AsyncSession, svc_role: Role
) -> RelationValidators:
    """Create RelationValidators instance."""
    return RelationValidators(session=session, workspace_id=svc_role.workspace_id)


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

        # Should raise for unsupported types
        with pytest.raises(ValidationError) as exc_info:
            FieldMetadataCreate(
                field_key="relation_field",
                field_type=FieldType.RELATION_BELONGS_TO,
                display_name="Relation Field",
                default_value="some_id",
                relation_settings=RelationSettings(
                    relation_type=RelationType.BELONGS_TO,
                    target_entity_id=UUID("00000000-0000-0000-0000-000000000001"),
                ),
            )
        assert "does not support default values" in str(exc_info.value)


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

    async def test_validate_record_data_flat_structure(
        self, record_validators: RecordValidators, test_field: FieldMetadata
    ):
        """Test that nested structures are rejected."""
        # Should raise for nested structure
        with pytest.raises(TracecatValidationError) as exc_info:
            await record_validators.validate_record_data(
                {"test_field": {"nested": "object"}},
                [test_field],
            )
        assert "nested objects not allowed" in str(exc_info.value).lower()


@pytest.mark.anyio
class TestRelationValidators:
    """Test relation validations."""

    async def test_validate_relation_settings_matches_field_type(self):
        """Test relation settings validation against field type using Pydantic model."""

        # Should pass for matching types
        belongs_to_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=UUID("00000000-0000-0000-0000-000000000001"),
        )
        field = FieldMetadataCreate(
            field_key="belongs_to_field",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Belongs To Field",
            relation_settings=belongs_to_settings,
        )
        assert field.relation_settings == belongs_to_settings

        has_many_settings = RelationSettings(
            relation_type=RelationType.HAS_MANY,
            target_entity_id=UUID("00000000-0000-0000-0000-000000000002"),
        )
        field = FieldMetadataCreate(
            field_key="has_many_field",
            field_type=FieldType.RELATION_HAS_MANY,
            display_name="Has Many Field",
            relation_settings=has_many_settings,
        )
        assert field.relation_settings == has_many_settings

        # Should raise for mismatched types
        with pytest.raises(ValidationError) as exc_info:
            FieldMetadataCreate(
                field_key="belongs_to_field",
                field_type=FieldType.RELATION_BELONGS_TO,
                display_name="Belongs To Field",
                relation_settings=has_many_settings,
            )
        assert "doesn't match field type" in str(exc_info.value)

        # Should raise for relation field without settings
        with pytest.raises(ValidationError) as exc_info:
            FieldMetadataCreate(
                field_key="belongs_to_field",
                field_type=FieldType.RELATION_BELONGS_TO,
                display_name="Belongs To Field",
                relation_settings=None,
            )
        assert "requires relation_settings" in str(exc_info.value)

    async def test_validate_target_entity_exists(
        self,
        relation_validators: RelationValidators,
        test_entity: Entity,
    ):
        """Test target entity validation."""
        # Should find existing entity
        result = await relation_validators.validate_target_entity(test_entity.name)
        assert result.id == test_entity.id

        # Should raise for non-existent entity
        with pytest.raises(TracecatNotFoundError) as exc_info:
            await relation_validators.validate_target_entity("non_existent")
        assert "not found" in str(exc_info.value)

    async def test_validate_target_entity_active(
        self,
        relation_validators: RelationValidators,
        test_entity: Entity,
        session: AsyncSession,
    ):
        """Test that inactive target entities are rejected."""
        # Deactivate entity
        test_entity.is_active = False
        session.add(test_entity)
        await session.commit()

        # Should raise for inactive entity
        with pytest.raises(TracecatValidationError) as exc_info:
            await relation_validators.validate_target_entity(test_entity.name)
        assert "not active" in str(exc_info.value)

    async def test_validate_target_record_exists_and_matches_entity(
        self,
        relation_validators: RelationValidators,
        test_record: Record,
        test_entity: Entity,
    ):
        """Test target record validation."""
        # Should find existing record in correct entity
        result = await relation_validators.validate_target_record(
            test_record.id, test_entity.id
        )
        assert result.id == test_record.id

        # Should raise for wrong entity
        fake_entity_id = UUID("00000000-0000-0000-0000-000000000000")
        with pytest.raises(TracecatNotFoundError):
            await relation_validators.validate_target_record(
                test_record.id, fake_entity_id
            )
