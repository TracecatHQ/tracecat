"""Tests for entity validation module."""

from uuid import UUID

import pytest
from pydantic_core import PydanticCustomError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    EntityData,
    EntityMetadata,
    EntityRelationLink,
    FieldMetadata,
    Workspace,
)
from tracecat.entities.models import RelationSettings
from tracecat.entities.types import FieldType
from tracecat.entities.validation import (
    ConstraintValidators,
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


@pytest.fixture
async def constraint_validators(
    session: AsyncSession, svc_role: Role
) -> ConstraintValidators:
    """Create ConstraintValidators instance."""
    return ConstraintValidators(session=session, workspace_id=svc_role.workspace_id)


@pytest.fixture
async def test_entity(
    session: AsyncSession, svc_workspace: Workspace
) -> EntityMetadata:
    """Create a test entity."""
    entity = EntityMetadata(
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
    session: AsyncSession, test_entity: EntityMetadata, svc_workspace: Workspace
) -> FieldMetadata:
    """Create a test field."""
    field = FieldMetadata(
        entity_metadata_id=test_entity.id,
        field_key="test_field",
        field_type=FieldType.TEXT.value,
        display_name="Test Field",
        owner_id=svc_workspace.id,
        is_active=True,
    )
    session.add(field)
    await session.commit()
    await session.refresh(field)
    return field


@pytest.fixture
async def test_record(
    session: AsyncSession,
    test_entity: EntityMetadata,
    svc_workspace: Workspace,
) -> EntityData:
    """Create a test record."""
    record = EntityData(
        entity_metadata_id=test_entity.id,
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
        self, entity_validators: EntityValidators, test_entity: EntityMetadata
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
        test_entity: EntityMetadata,
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
        test_entity: EntityMetadata,
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
        test_entity: EntityMetadata,
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

    async def test_validate_enum_options_for_select_fields(
        self, field_validators: FieldValidators
    ):
        """Test enum options validation for SELECT fields."""
        # Should pass for SELECT with options
        field_validators.validate_enum_options_for_type(
            FieldType.SELECT, ["option1", "option2"]
        )

        # Should pass for MULTI_SELECT with options
        field_validators.validate_enum_options_for_type(
            FieldType.MULTI_SELECT, ["option1", "option2"]
        )

        # Should raise for SELECT without options
        with pytest.raises(PydanticCustomError) as exc_info:
            field_validators.validate_enum_options_for_type(FieldType.SELECT, None)
        assert "requires enum_options" in str(exc_info.value)

    async def test_validate_enum_options_rejects_non_select_fields(
        self, field_validators: FieldValidators
    ):
        """Test enum options validation rejects non-SELECT fields."""
        # Should raise for non-SELECT field with options
        with pytest.raises(PydanticCustomError) as exc_info:
            field_validators.validate_enum_options_for_type(FieldType.TEXT, ["option1"])
        assert "cannot have enum_options" in str(exc_info.value)

        # Should pass for non-SELECT field without options
        field_validators.validate_enum_options_for_type(FieldType.TEXT, None)

    async def test_validate_field_type_supports_unique(
        self, field_validators: FieldValidators
    ):
        """Test validation of field types that support unique constraint."""
        # Should pass for TEXT and NUMBER
        field_validators.validate_field_type_supports_unique(FieldType.TEXT)
        field_validators.validate_field_type_supports_unique(FieldType.NUMBER)

        # Should raise for unsupported types
        with pytest.raises(PydanticCustomError) as exc_info:
            field_validators.validate_field_type_supports_unique(FieldType.BOOL)
        assert "does not support unique constraint" in str(exc_info.value)

    async def test_validate_field_type_supports_default(
        self, field_validators: FieldValidators
    ):
        """Test validation of field types that support default values."""
        # Should pass for most types
        field_validators.validate_field_type_supports_default(FieldType.TEXT)
        field_validators.validate_field_type_supports_default(FieldType.NUMBER)
        field_validators.validate_field_type_supports_default(FieldType.BOOL)

        # Should raise for relation types
        with pytest.raises(PydanticCustomError) as exc_info:
            field_validators.validate_field_type_supports_default(
                FieldType.RELATION_BELONGS_TO
            )
        assert "does not support default values" in str(exc_info.value)


@pytest.mark.anyio
class TestRecordValidators:
    """Test record-level validations."""

    async def test_validate_record_exists(
        self,
        record_validators: RecordValidators,
        test_record: EntityData,
        test_entity: EntityMetadata,
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

    async def test_check_unique_violation_detects_duplicate(
        self,
        record_validators: RecordValidators,
        test_entity: EntityMetadata,
        test_record: EntityData,
    ):
        """Test detecting unique constraint violations."""
        # Should detect duplicate
        has_duplicate = await record_validators.check_unique_violation(
            test_entity.id, "test_field", "test_value"
        )
        assert has_duplicate is True

        # Should not detect duplicate for different value
        has_duplicate = await record_validators.check_unique_violation(
            test_entity.id, "test_field", "different_value"
        )
        assert has_duplicate is False

    async def test_check_unique_violation_excludes_current_record(
        self,
        record_validators: RecordValidators,
        test_entity: EntityMetadata,
        test_record: EntityData,
    ):
        """Test unique violation check excludes current record."""
        # Should not detect duplicate when excluding current record
        has_duplicate = await record_validators.check_unique_violation(
            test_entity.id,
            "test_field",
            "test_value",
            exclude_record_id=test_record.id,
        )
        assert has_duplicate is False

    async def test_validate_required_fields(
        self,
        record_validators: RecordValidators,
        test_entity: EntityMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test required field validation."""
        # Create required field
        required_field = FieldMetadata(
            entity_metadata_id=test_entity.id,
            field_key="required_field",
            field_type=FieldType.TEXT.value,
            display_name="Required Field",
            is_required=True,
            is_active=True,
            owner_id=svc_workspace.id,
        )
        session.add(required_field)
        await session.commit()

        # Should raise for missing required field
        with pytest.raises(TracecatValidationError) as exc_info:
            record_validators.validate_required_fields({}, [required_field])
        assert "Required field" in str(exc_info.value)

        # Should pass with required field present
        record_validators.validate_required_fields(
            {"required_field": "value"}, [required_field]
        )

    async def test_validate_record_data_type_checking(
        self,
        record_validators: RecordValidators,
        test_entity: EntityMetadata,
        test_field: FieldMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test record data type validation."""
        # Create number field
        number_field = FieldMetadata(
            entity_metadata_id=test_entity.id,
            field_key="number_field",
            field_type=FieldType.NUMBER.value,
            display_name="Number Field",
            is_active=True,
            owner_id=svc_workspace.id,
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
        assert "flat structure" in str(exc_info.value).lower()


@pytest.mark.anyio
class TestRelationValidators:
    """Test relation validations."""

    async def test_validate_relation_settings_matches_field_type(
        self, relation_validators: RelationValidators
    ):
        """Test relation settings validation against field type."""
        # Should pass for matching types
        belongs_to_settings = RelationSettings(
            relation_type="belongs_to",
            target_entity_name="target",
        )
        relation_validators.validate_relation_settings(
            FieldType.RELATION_BELONGS_TO, belongs_to_settings
        )

        has_many_settings = RelationSettings(
            relation_type="has_many",
            target_entity_name="target",
        )
        relation_validators.validate_relation_settings(
            FieldType.RELATION_HAS_MANY, has_many_settings
        )

        # Should raise for mismatched types
        with pytest.raises(PydanticCustomError):
            relation_validators.validate_relation_settings(
                FieldType.RELATION_BELONGS_TO, has_many_settings
            )

        # Should raise for relation field without settings
        with pytest.raises(PydanticCustomError):
            relation_validators.validate_relation_settings(
                FieldType.RELATION_BELONGS_TO, None
            )

    async def test_validate_target_entity_exists(
        self,
        relation_validators: RelationValidators,
        test_entity: EntityMetadata,
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
        test_entity: EntityMetadata,
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
        test_record: EntityData,
        test_entity: EntityMetadata,
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

    async def test_validate_unique_relation_constraint(
        self,
        relation_validators: RelationValidators,
        test_entity: EntityMetadata,
        test_record: EntityData,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test unique constraint validation for has-many relations."""
        # Create has-many field with unique constraint
        has_many_field = FieldMetadata(
            entity_metadata_id=test_entity.id,
            field_key="unique_relation",
            field_type=FieldType.RELATION_HAS_MANY.value,
            display_name="Unique Relation",
            is_unique=True,
            relation_settings={
                "relation_type": "has_many",
                "target_entity_name": test_entity.name,
            },
            owner_id=svc_workspace.id,
        )
        session.add(has_many_field)
        await session.commit()

        # Create target record
        target_record = EntityData(
            entity_metadata_id=test_entity.id,
            field_data={},
            owner_id=svc_workspace.id,
        )
        session.add(target_record)
        await session.commit()

        # Should pass for first link
        await relation_validators.validate_unique_relation(
            has_many_field, target_record.id
        )

        # Create existing link
        link = EntityRelationLink(
            field_metadata_id=has_many_field.id,
            source_entity_data_id=test_record.id,
            target_entity_data_id=target_record.id,
            owner_id=svc_workspace.id,
        )
        session.add(link)
        await session.commit()

        # Should raise for duplicate link
        other_record = EntityData(
            entity_metadata_id=test_entity.id,
            field_data={},
            owner_id=svc_workspace.id,
        )
        session.add(other_record)
        await session.commit()

        with pytest.raises(TracecatValidationError) as exc_info:
            await relation_validators.validate_unique_relation(
                has_many_field, target_record.id, exclude_record_id=other_record.id
            )
        assert "already linked" in str(exc_info.value)


@pytest.mark.anyio
class TestConstraintValidators:
    """Test constraint change validations."""

    async def test_validate_unique_constraint_change_with_duplicates(
        self,
        constraint_validators: ConstraintValidators,
        test_entity: EntityMetadata,
        test_field: FieldMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test unique constraint validation with duplicate values."""
        # Create records with duplicate values
        for _ in range(2):
            record = EntityData(
                entity_metadata_id=test_entity.id,
                field_data={"test_field": "duplicate_value"},
                owner_id=svc_workspace.id,
            )
            session.add(record)
        await session.commit()

        # Should raise when duplicates exist
        with pytest.raises(ValueError) as exc_info:
            await constraint_validators.validate_unique_constraint_change(test_field)
        assert "Duplicate values found" in str(exc_info.value)
        assert "duplicate_value" in str(exc_info.value)

    async def test_validate_unique_constraint_change_without_duplicates(
        self,
        constraint_validators: ConstraintValidators,
        test_entity: EntityMetadata,
        test_field: FieldMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test unique constraint validation without duplicates."""
        # Create records with unique values
        for i in range(3):
            record = EntityData(
                entity_metadata_id=test_entity.id,
                field_data={"test_field": f"unique_value_{i}"},
                owner_id=svc_workspace.id,
            )
            session.add(record)
        await session.commit()

        # Should pass when all values are unique
        await constraint_validators.validate_unique_constraint_change(test_field)

    async def test_validate_required_constraint_change_with_nulls(
        self,
        constraint_validators: ConstraintValidators,
        test_entity: EntityMetadata,
        test_field: FieldMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test required constraint validation with null values."""
        # Create records with null/missing values
        record1 = EntityData(
            entity_metadata_id=test_entity.id,
            field_data={},  # Missing field
            owner_id=svc_workspace.id,
        )
        record2 = EntityData(
            entity_metadata_id=test_entity.id,
            field_data={"test_field": None},  # Null value
            owner_id=svc_workspace.id,
        )
        session.add(record1)
        session.add(record2)
        await session.commit()

        # Should raise when nulls exist
        with pytest.raises(ValueError) as exc_info:
            await constraint_validators.validate_required_constraint_change(test_field)
        assert "2 records have null or missing values" in str(exc_info.value)

    async def test_validate_required_constraint_change_without_nulls(
        self,
        constraint_validators: ConstraintValidators,
        test_entity: EntityMetadata,
        test_field: FieldMetadata,
        session: AsyncSession,
        svc_workspace: Workspace,
    ):
        """Test required constraint validation without null values."""
        # Create records with non-null values
        for i in range(3):
            record = EntityData(
                entity_metadata_id=test_entity.id,
                field_data={"test_field": f"value_{i}"},
                owner_id=svc_workspace.id,
            )
            session.add(record)
        await session.commit()

        # Should pass when all values are non-null
        await constraint_validators.validate_required_constraint_change(test_field)
