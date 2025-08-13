import uuid
from datetime import date, datetime
from unittest.mock import patch

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import EntityData, EntityMetadata, FieldMetadata
from tracecat.entities.models import RelationSettings, RelationType
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def entities_service(
    session: AsyncSession, svc_role: Role
) -> CustomEntitiesService:
    """Create an entities service instance for testing."""
    return CustomEntitiesService(session=session, role=svc_role)


@pytest.fixture
async def admin_entities_service(
    session: AsyncSession, svc_admin_role: Role
) -> CustomEntitiesService:
    """Create an entities service instance with admin role for testing."""
    return CustomEntitiesService(session=session, role=svc_admin_role)


@pytest.fixture
def entity_create_params() -> dict:
    """Sample entity type creation parameters."""
    return {
        "name": "test_entity",
        "display_name": "Test Entity",
        "description": "Test entity for unit testing",
        "icon": "test-icon",
        "settings": {"test_setting": "value"},
    }


@pytest.fixture
def field_create_params() -> dict:
    """Sample field creation parameters."""
    return {
        "field_key": "test_field",
        "field_type": FieldType.TEXT,
        "display_name": "Test Field",
        "description": "Test field for unit testing",
        "settings": {"max_length": 100},
    }


@pytest.fixture
async def test_entity(admin_entities_service: CustomEntitiesService) -> EntityMetadata:
    """Create a test entity type."""
    return await admin_entities_service.create_entity_type(
        name="test_entity",
        display_name="Test Entity",
        description="Entity for testing constraints",
    )


@pytest.fixture
def integration_entity_create_params() -> dict:
    """Sample entity type creation parameters for integration tests."""
    return {
        "name": "customer",
        "display_name": "Customer",
        "description": "Customer entity for integration testing",
        "icon": "user-icon",
        "settings": {"default_view": "table"},
    }


@pytest.mark.anyio
class TestDefaultValues:
    """Tests for field default value functionality."""

    async def test_create_field_with_text_default(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test creating a field with a text default value."""
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="text_with_default",
            field_type=FieldType.TEXT,
            display_name="Text with Default",
            default_value="Default text value",
        )

        assert field.default_value == "Default text value"
        assert field.field_type == FieldType.TEXT.value

    async def test_create_field_with_integer_default(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test creating a field with an integer default value."""
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="int_with_default",
            field_type=FieldType.INTEGER,
            display_name="Integer with Default",
            default_value=42,
        )

        assert field.default_value == 42
        assert field.field_type == FieldType.INTEGER.value

    async def test_create_field_with_bool_default(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test creating a field with a boolean default value."""
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="bool_with_default",
            field_type=FieldType.BOOL,
            display_name="Boolean with Default",
            default_value=True,
        )

        assert field.default_value is True
        assert field.field_type == FieldType.BOOL.value

    async def test_create_field_with_multi_select_default(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test creating a field with a multi-select default value."""
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="multi_with_default",
            field_type=FieldType.MULTI_SELECT,
            display_name="Multi-Select with Default",
            enum_options=["option1", "option2", "option3"],
            default_value=["option1", "option2"],
        )

        assert field.default_value == ["option1", "option2"]
        assert field.field_type == FieldType.MULTI_SELECT.value

    async def test_create_field_rejects_non_primitive_default(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test that non-primitive field types cannot have default values."""
        with pytest.raises(
            ValueError, match="Default values not supported for field type"
        ):
            await admin_entities_service.create_field(
                entity_id=test_entity.id,
                field_key="array_field",
                field_type=FieldType.ARRAY_TEXT,
                display_name="Array Field",
                default_value=["item1", "item2"],
            )

    async def test_create_record_applies_defaults(
        self,
        entities_service: CustomEntitiesService,
        admin_entities_service: CustomEntitiesService,
        test_entity: EntityMetadata,
    ) -> None:
        """Test that default values are applied when creating records."""
        # Create fields with defaults
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
            default_value="Unnamed",
        )
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="count",
            field_type=FieldType.INTEGER,
            display_name="Count",
            default_value=0,
        )
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="active",
            field_type=FieldType.BOOL,
            display_name="Active",
            default_value=True,
        )

        # Create record with only one field provided
        record = await entities_service.create_record(
            entity_id=test_entity.id,
            data={"count": 5},  # Only provide count, others should get defaults
        )

        assert record.field_data["name"] == "Unnamed"
        assert record.field_data["count"] == 5  # Provided value
        assert record.field_data["active"] is True

    async def test_required_field_with_default_passes_validation(
        self,
        entities_service: CustomEntitiesService,
        admin_entities_service: CustomEntitiesService,
        test_entity: EntityMetadata,
    ) -> None:
        """Test that required fields with defaults pass validation even when not provided."""
        # Create a required field with a default
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="required_field",
            field_type=FieldType.TEXT,
            display_name="Required Field",
            is_required=True,
            default_value="Default required value",
        )

        # Should be able to create a record without providing the required field
        record = await entities_service.create_record(
            entity_id=test_entity.id,
            data={},  # No fields provided
        )

        assert record.field_data["required_field"] == "Default required value"

    async def test_update_field_default_value(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test updating a field's default value."""
        # Create field with initial default
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="updatable_field",
            field_type=FieldType.TEXT,
            display_name="Updatable Field",
            default_value="Initial default",
        )

        # Update the default value
        updated_field = await admin_entities_service.update_field(
            field_id=field.id,
            default_value="Updated default",
        )

        assert updated_field.default_value == "Updated default"

    async def test_select_default_validates_against_options(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test that SELECT field default value must be one of the options."""
        with pytest.raises(ValueError, match="not in available options"):
            await admin_entities_service.create_field(
                entity_id=test_entity.id,
                field_key="select_field",
                field_type=FieldType.SELECT,
                display_name="Select Field",
                enum_options=["option1", "option2", "option3"],
                default_value="invalid_option",
            )

    async def test_multi_select_default_validates_against_options(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ) -> None:
        """Test that MULTI_SELECT field default values must be from the options."""
        with pytest.raises(ValueError, match="not in available options"):
            await admin_entities_service.create_field(
                entity_id=test_entity.id,
                field_key="multi_select_field",
                field_type=FieldType.MULTI_SELECT,
                display_name="Multi-Select Field",
                enum_options=["option1", "option2", "option3"],
                default_value=["option1", "invalid_option"],
            )


@pytest.mark.anyio
class TestCustomEntitiesService:
    async def test_init_requires_workspace_id(self, session: AsyncSession) -> None:
        """Test that service initialization requires a workspace ID."""
        # Create a role without workspace_id
        role_without_workspace = Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=None,
            service_id="tracecat-service",
            access_level=AccessLevel.BASIC,
        )

        # Attempt to create service without workspace should raise error
        with pytest.raises(TracecatAuthorizationError):
            CustomEntitiesService(session=session, role=role_without_workspace)

    async def test_create_entity_type_invalid_name(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating entity type with invalid name format."""
        # Test various invalid names with expected error messages
        invalid_cases = [
            (
                "Test Entity",
                "Field key must be alphanumeric with underscores only",
            ),  # spaces before lowercase check
            (
                "test entity",
                "Field key must be alphanumeric with underscores only",
            ),  # spaces
            (
                "test-entity",
                "Field key must be alphanumeric with underscores only",
            ),  # hyphens
            (
                "123test",
                "Field key must start with a letter",
            ),  # must start with letter
            (
                "test@entity",
                "Field key must be alphanumeric with underscores only",
            ),  # special chars
            ("_test", "Field key must start with a letter"),  # must start with letter
            ("", "Field key cannot be empty"),  # empty string
            (
                "TestEntity",
                "Field key must be lowercase",
            ),  # uppercase without special chars
        ]

        for invalid_name, expected_error in invalid_cases:
            with pytest.raises(ValueError, match=expected_error):
                await admin_entities_service.create_entity_type(
                    name=invalid_name,
                    display_name="Display Name",
                    description="Description",
                )

    async def test_create_field_invalid_key(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test creating field with invalid key format."""
        # First create an entity type
        entity = EntityMetadata(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        # Test various invalid field keys with expected error messages
        invalid_cases = [
            (
                "Test Field",
                "Field key must be alphanumeric with underscores only",
            ),  # spaces before lowercase check
            (
                "test field",
                "Field key must be alphanumeric with underscores only",
            ),  # spaces
            (
                "test-field",
                "Field key must be alphanumeric with underscores only",
            ),  # hyphens
            ("123test", "Field key must start with a letter"),  # must start with letter
            (
                "test@field",
                "Field key must be alphanumeric with underscores only",
            ),  # special chars
            ("_test", "Field key must start with a letter"),  # must start with letter
            ("", "Field key cannot be empty"),  # empty string
            (
                "TestField",
                "Field key must be lowercase",
            ),  # uppercase without special chars
        ]

        for invalid_key, expected_error in invalid_cases:
            with pytest.raises(ValueError, match=expected_error):
                await admin_entities_service.create_field(
                    entity_id=entity.id,
                    field_key=invalid_key,
                    field_type=FieldType.TEXT,
                    display_name="Display Name",
                )

    async def test_create_field_with_nested_settings(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that field settings have been removed - nested structures handled differently."""
        # Create an entity type
        entity = EntityMetadata(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        # Create field - no longer has settings parameter
        field = await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
        )

        # Verify field was created without settings
        assert field.field_key == "test_field"
        assert field.field_type == FieldType.TEXT

    async def test_update_field_display_properties_only(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that only display properties can be updated, not schema."""
        # Create entity and field
        entity = EntityMetadata(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Original Display Name",
            description="Original Description",
            # field_settings removed - using dedicated columns now
            is_active=True,
            is_required=False,
        )
        session.add(field)
        await session.commit()

        # Update only display properties
        updated = await admin_entities_service.update_field(
            field_id=field.id,
            display_name="Updated Display Name",
            description="Updated Description",
            # settings parameter removed
        )

        # Verify display properties were updated
        assert updated.display_name == "Updated Display Name"
        assert updated.description == "Updated Description"
        # field_settings removed - no configurable settings in v1

        # Verify immutable properties remain unchanged
        assert updated.field_key == "test_field"
        assert updated.field_type == FieldType.TEXT

    async def test_deactivate_field_already_inactive(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test deactivating an already inactive field raises error."""
        # Create entity and inactive field
        entity = EntityMetadata(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=False,  # Already inactive
            is_required=False,
        )
        session.add(field)
        await session.commit()

        # Try to deactivate already inactive field
        with pytest.raises(ValueError, match="Field is already inactive"):
            await admin_entities_service.deactivate_field(field.id)

    async def test_reactivate_field_already_active(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test reactivating an already active field raises error."""
        # Create entity and active field
        entity = EntityMetadata(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=True,  # Already active
            is_required=False,
        )
        session.add(field)
        await session.commit()

        # Try to reactivate already active field
        with pytest.raises(ValueError, match="Field is already active"):
            await admin_entities_service.reactivate_field(field.id)

    async def test_create_record_with_nested_structure(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that creating record with nested objects is rejected."""
        # Create entity and field
        entity = EntityMetadata(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=True,
            is_required=False,
        )
        session.add(field)
        await session.commit()

        # Try to create record with nested object
        with pytest.raises(TracecatValidationError, match="Nested objects not allowed"):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"test_field": {"nested": "object"}},  # Nested object not allowed
            )

    async def test_validate_record_data_with_inactive_fields(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that inactive fields are ignored during validation."""
        # Create entity with active and inactive fields
        entity = EntityMetadata(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        active_field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="active_field",
            field_type=FieldType.TEXT,
            display_name="Active Field",
            is_active=True,
            is_required=False,
        )
        inactive_field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="inactive_field",
            field_type=FieldType.TEXT,
            display_name="Inactive Field",
            is_active=False,  # Inactive
            is_required=False,
        )
        session.add(active_field)
        session.add(inactive_field)
        await session.commit()

        # Create record with both active and inactive field data
        record = await entities_service.create_record(
            entity_id=entity.id,
            data={
                "active_field": "active value",
                "inactive_field": "inactive value",  # Should be ignored
                "unknown_field": "unknown value",  # Should be ignored
            },
        )

        # Verify only active field was saved
        assert "active_field" in record.field_data
        assert record.field_data["active_field"] == "active value"
        assert "inactive_field" not in record.field_data
        assert "unknown_field" not in record.field_data

    async def test_get_entity_type_not_found(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test getting non-existent entity type raises error."""
        non_existent_id = uuid.uuid4()
        with pytest.raises(
            TracecatNotFoundError, match=f"Entity type {non_existent_id} not found"
        ):
            await entities_service.get_entity_type(non_existent_id)

    async def test_get_field_not_found(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test getting non-existent field raises error."""
        non_existent_id = uuid.uuid4()
        with pytest.raises(
            TracecatNotFoundError, match=f"Field {non_existent_id} not found"
        ):
            await entities_service.get_field(non_existent_id)

    async def test_get_record_not_found(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test getting non-existent record raises error."""
        non_existent_id = uuid.uuid4()
        with pytest.raises(
            TracecatNotFoundError, match=f"Record {non_existent_id} not found"
        ):
            await entities_service.get_record(non_existent_id)

    async def test_query_records_with_filters(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test querying records with filters uses query builder."""
        # Create entity
        entity = EntityMetadata(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        # Mock the query builder to return the base statement unchanged
        with patch.object(
            entities_service.query_builder, "build_query"
        ) as mock_build_query:
            # Return the input statement unchanged
            async def mock_build(stmt, entity_id, filters):
                return stmt

            mock_build_query.side_effect = mock_build

            # Query with filters
            filters = [{"field": "test_field", "operator": "eq", "value": "test"}]
            result = await entities_service.query_records(
                entity_id=entity.id, filters=filters, limit=10, offset=0
            )

            # Verify query builder was called with filters
            mock_build_query.assert_called_once()
            call_args = mock_build_query.call_args
            assert call_args[0][1] == entity.id
            assert call_args[0][2] == filters

            # Result should be empty since no records exist
            assert result == []

    async def test_get_active_fields_model(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test dynamic Pydantic model generation for active fields."""
        # Create test fields with different types
        fields = [
            FieldMetadata(
                entity_metadata_id=uuid.uuid4(),
                field_key="text_field",
                field_type=FieldType.TEXT,
                display_name="Text Field",
                description="A text field",
                is_active=True,
                is_required=False,
            ),
            FieldMetadata(
                entity_metadata_id=uuid.uuid4(),
                field_key="number_field",
                field_type=FieldType.NUMBER,
                display_name="Number Field",
                description="A number field",
                is_active=True,
                is_required=False,
            ),
            FieldMetadata(
                entity_metadata_id=uuid.uuid4(),
                field_key="inactive_field",
                field_type=FieldType.TEXT,
                display_name="Inactive Field",
                is_active=False,  # Inactive - should be excluded
                is_required=False,
            ),
        ]

        # Generate model
        model = entities_service.get_active_fields_model(fields)

        # Verify model has correct fields
        assert "text_field" in model.model_fields
        assert "number_field" in model.model_fields
        assert "inactive_field" not in model.model_fields  # Excluded

        # Verify model rejects extra fields
        from pydantic import ValidationError

        with pytest.raises(ValidationError):  # Pydantic will raise validation error
            model(text_field="test", unknown_field="should fail")

    async def test_admin_access_required_for_create_entity(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test that creating entity type requires admin access."""
        # entities_service has basic role, should fail
        with pytest.raises(TracecatAuthorizationError):
            await entities_service.create_entity_type(
                name="test_entity",
                display_name="Test Entity",
                description="Should fail",
            )

    async def test_admin_access_required_for_create_field(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that creating field requires admin access."""
        # Create entity first (bypass service for setup)
        entity = EntityMetadata(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        # entities_service has basic role, should fail
        with pytest.raises(TracecatAuthorizationError):
            await entities_service.create_field(
                entity_id=entity.id,
                field_key="test_field",
                field_type=FieldType.TEXT,
                display_name="Test Field",
            )

    async def test_admin_access_required_for_deactivate_field(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that deactivating field requires admin access."""
        # Create entity and field (bypass service for setup)
        entity = EntityMetadata(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_metadata_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=True,
            is_required=False,
        )
        session.add(field)
        await session.commit()

        # entities_service has basic role, should fail
        with pytest.raises(TracecatAuthorizationError):
            await entities_service.deactivate_field(field.id)


@pytest.mark.anyio
class TestConstraintValidationMethods:
    """Test the internal validation methods are called correctly."""

    async def test_validate_record_data_called_with_record_id_on_update(
        self, admin_entities_service: CustomEntitiesService, mocker
    ):
        """Test that update_record passes record_id to validation."""
        # Create entity and record
        entity = await admin_entities_service.create_entity_type(
            name="test_entity", display_name="Test Entity"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
        )

        record = await admin_entities_service.create_record(
            entity_id=entity.id, data={"test_field": "initial"}
        )

        # Spy on record_validators.validate_record_data
        spy = mocker.spy(
            admin_entities_service.record_validators, "validate_record_data"
        )

        # Update the record
        await admin_entities_service.update_record(
            record_id=record.id, updates={"test_field": "updated"}
        )

        # Verify validate_record_data was called with exclude_record_id
        spy.assert_called()
        # Get the last call (there might be multiple calls due to create_record)
        last_call = spy.call_args_list[-1]
        assert last_call[1]["exclude_record_id"] == record.id

    async def test_validate_record_data_called_with_none_on_create(
        self, admin_entities_service: CustomEntitiesService, mocker
    ):
        """Test that create_record passes record_id=None to validation."""
        # Create entity
        entity = await admin_entities_service.create_entity_type(
            name="test_entity", display_name="Test Entity"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
        )

        # Spy on record_validators.validate_record_data
        spy = mocker.spy(
            admin_entities_service.record_validators, "validate_record_data"
        )

        # Create a record
        await admin_entities_service.create_record(
            entity_id=entity.id, data={"test_field": "value"}
        )

        # Verify validate_record_data was called with exclude_record_id=None
        spy.assert_called_once()
        call_args = spy.call_args
        assert call_args[1]["exclude_record_id"] is None


@pytest.mark.anyio
class TestEntitiesIntegration:
    """Integration tests for complete entity lifecycle."""

    async def test_complete_entity_lifecycle(
        self,
        admin_entities_service: CustomEntitiesService,
        integration_entity_create_params: dict,
    ) -> None:
        """Test complete lifecycle: create entity -> add fields -> create records -> query."""
        # Step 1: Create entity type
        entity = await admin_entities_service.create_entity_type(
            **integration_entity_create_params
        )
        assert entity.name == integration_entity_create_params["name"]
        assert entity.display_name == integration_entity_create_params["display_name"]
        assert entity.owner_id == admin_entities_service.workspace_id

        # Step 2: Add fields of various types
        text_field = await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Customer Name",
            description="Full name of the customer",
            # No configurable max_length in v1
        )
        assert text_field.field_key == "name"
        assert text_field.field_type == FieldType.TEXT

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="age",
            field_type=FieldType.INTEGER,
            display_name="Age",
            # No configurable min/max for integers in v1
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="status",
            field_type=FieldType.SELECT,
            display_name="Status",
            enum_options=["active", "inactive", "pending"],
        )

        # Step 3: Create records
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "name": "John Doe",
                "age": 30,
                "status": "active",
            },
        )
        assert record1.entity_metadata_id == entity.id
        assert record1.field_data["name"] == "John Doe"
        assert record1.field_data["age"] == 30
        assert record1.field_data["status"] == "active"

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "name": "Jane Smith",
                "age": 25,
                "status": "pending",
            },
        )

        # Step 4: Query records
        all_records = await admin_entities_service.query_records(entity_id=entity.id)
        assert len(all_records) == 2
        record_ids = {r.id for r in all_records}
        assert record1.id in record_ids
        assert record2.id in record_ids

        # Step 5: Update a record
        updated_record = await admin_entities_service.update_record(
            record_id=record1.id, updates={"status": "inactive", "age": 31}
        )
        assert updated_record.field_data["status"] == "inactive"
        assert updated_record.field_data["age"] == 31
        assert updated_record.field_data["name"] == "John Doe"  # Unchanged

        # Step 6: Delete a record
        await admin_entities_service.delete_record(record2.id)
        remaining_records = await admin_entities_service.query_records(
            entity_id=entity.id
        )
        assert len(remaining_records) == 1
        assert remaining_records[0].id == record1.id

    async def test_create_entity_with_multiple_field_types(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test creating entity with all supported field types."""
        # Create entity
        entity = await admin_entities_service.create_entity_type(
            name="test_all_types",
            display_name="Test All Field Types",
            description="Entity with all field types",
        )

        # Add all field types
        field_configs = [
            ("integer_field", FieldType.INTEGER, {"min": 0, "max": 1000}),
            ("number_field", FieldType.NUMBER, {"min": 0.0, "max": 100.0}),
            ("text_field", FieldType.TEXT, {"max_length": 500}),
            ("bool_field", FieldType.BOOL, {}),
            ("date_field", FieldType.DATE, {}),
            ("datetime_field", FieldType.DATETIME, {}),
            ("array_text_field", FieldType.ARRAY_TEXT, {"max_items": 10}),
            ("array_int_field", FieldType.ARRAY_INTEGER, {"max_items": 5}),
            ("array_num_field", FieldType.ARRAY_NUMBER, {"max_items": 5}),
            ("select_field", FieldType.SELECT, {"options": ["opt1", "opt2", "opt3"]}),
            (
                "multi_select_field",
                FieldType.MULTI_SELECT,
                {"options": ["tag1", "tag2", "tag3"]},
            ),
        ]

        created_fields = []
        for field_key, field_type, settings in field_configs:
            # Extract enum_options for SELECT/MULTI_SELECT fields
            enum_options = None
            if field_type in (FieldType.SELECT, FieldType.MULTI_SELECT):
                enum_options = settings.get("options")

            field = await admin_entities_service.create_field(
                entity_id=entity.id,
                field_key=field_key,
                field_type=field_type,
                display_name=field_key.replace("_", " ").title(),
                enum_options=enum_options,
            )
            created_fields.append(field)

        # Verify all fields were created
        all_fields = await admin_entities_service.list_fields(entity_id=entity.id)
        assert len(all_fields) == len(field_configs)

        # Create a record with all field types
        test_date = date.today()
        test_datetime = datetime.now()

        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "integer_field": 42,
                "number_field": 3.14,
                "text_field": "Test text value",
                "bool_field": True,
                "date_field": test_date.isoformat(),
                "datetime_field": test_datetime.isoformat(),
                "array_text_field": ["item1", "item2", "item3"],
                "array_int_field": [1, 2, 3],
                "array_num_field": [1.1, 2.2, 3.3],
                "select_field": "opt2",
                "multi_select_field": ["tag1", "tag3"],
            },
        )

        # Verify all values were stored correctly
        assert record.field_data["integer_field"] == 42
        assert record.field_data["number_field"] == 3.14
        assert record.field_data["text_field"] == "Test text value"
        assert record.field_data["bool_field"] is True
        assert record.field_data["date_field"] == test_date.isoformat()
        assert record.field_data["array_text_field"] == ["item1", "item2", "item3"]

    async def test_field_immutability_after_creation(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that field key and type are immutable after creation."""
        # Create entity and field
        entity = await admin_entities_service.create_entity_type(
            name="immutable_test",
            display_name="Immutability Test",
        )

        field = await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="original_key",
            field_type=FieldType.TEXT,
            display_name="Original Display Name",
            description="Original Description",
        )

        original_key = field.field_key
        original_type = field.field_type

        # Update display properties only
        updated = await admin_entities_service.update_field(
            field_id=field.id,
            display_name="New Display Name",
            description="New Description",
            # No configurable max_length in v1
        )

        # Verify display properties changed
        assert updated.display_name == "New Display Name"
        assert updated.description == "New Description"

        # Verify key and type are unchanged
        assert updated.field_key == original_key
        assert updated.field_type == original_type

        # Verify in database
        stmt = select(FieldMetadata).where(FieldMetadata.id == field.id)
        result = await session.exec(stmt)
        db_field = result.one()
        assert db_field.field_key == original_key
        assert db_field.field_type == original_type

    async def test_soft_delete_preserves_data(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that soft-deleting fields preserves existing data."""
        # Create entity with fields
        entity = await admin_entities_service.create_entity_type(
            name="soft_delete_test",
            display_name="Soft Delete Test",
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="keep_field",
            field_type=FieldType.TEXT,
            display_name="Keep Field",
        )

        field2 = await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="delete_field",
            field_type=FieldType.TEXT,
            display_name="Delete Field",
        )

        # Create record with both fields
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "keep_field": "keep this data",
                "delete_field": "soft delete this field",
            },
        )

        # Soft delete field2
        deactivated = await admin_entities_service.deactivate_field(field2.id)
        assert deactivated.is_active is False
        assert deactivated.deactivated_at is not None

        # Verify field data is still in database
        stmt = select(EntityData).where(EntityData.id == record.id)
        result = await session.exec(stmt)
        db_record = result.one()
        assert "delete_field" in db_record.field_data
        assert db_record.field_data["delete_field"] == "soft delete this field"

        # Try to create new record - deleted field should be ignored
        new_record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "keep_field": "new keep data",
                "delete_field": "this should be ignored",  # Inactive field
            },
        )
        assert "keep_field" in new_record.field_data
        assert "delete_field" not in new_record.field_data

        # Reactivate field
        reactivated = await admin_entities_service.reactivate_field(field2.id)
        assert reactivated.is_active is True
        assert reactivated.deactivated_at is None

        # Now the field should work again
        record_with_reactivated = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "keep_field": "keep data",
                "delete_field": "field is active again",
            },
        )
        assert "delete_field" in record_with_reactivated.field_data
        assert (
            record_with_reactivated.field_data["delete_field"]
            == "field is active again"
        )

    async def test_inactive_fields_excluded_from_validation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that inactive fields are excluded from validation."""
        # Create entity with active and inactive fields
        entity = await admin_entities_service.create_entity_type(
            name="validation_test",
            display_name="Validation Test",
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="active",
            field_type=FieldType.INTEGER,
            display_name="Active Field",
            # No configurable min/max for integers in v1
        )

        inactive_field = await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="inactive",
            field_type=FieldType.INTEGER,
            display_name="Inactive Field",
            # No configurable min/max for integers in v1
        )

        # Deactivate the inactive field
        await admin_entities_service.deactivate_field(inactive_field.id)

        # Create record with invalid value for inactive field (should be ignored)
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "active": 50,  # Valid
                "inactive": 200,  # Invalid but should be ignored
                "unknown": "also ignored",  # Unknown field
            },
        )

        # Only active field should be in the record
        assert "active" in record.field_data
        assert record.field_data["active"] == 50
        assert "inactive" not in record.field_data
        assert "unknown" not in record.field_data

    async def test_flat_structure_enforcement(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that nested structures are rejected."""
        entity = await admin_entities_service.create_entity_type(
            name="flat_test",
            display_name="Flat Structure Test",
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="data_field",
            field_type=FieldType.TEXT,
            display_name="Data Field",
        )

        # Test nested object rejection
        with pytest.raises(TracecatValidationError, match="Nested objects not allowed"):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={
                    "data_field": {"nested": "object"},  # Not allowed
                },
            )

        # Test nested array rejection
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="array_field",
            field_type=FieldType.ARRAY_TEXT,
            display_name="Array Field",
        )

        with pytest.raises(TracecatValidationError, match="Nested objects not allowed"):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={
                    "array_field": [["nested", "array"]],  # Not allowed
                },
            )

    async def test_field_type_validation_all_types(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test validation for all field types."""
        entity = await admin_entities_service.create_entity_type(
            name="validation_all_types",
            display_name="Validation All Types",
        )

        # INTEGER validation
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="int_field",
            field_type=FieldType.INTEGER,
            display_name="Integer Field",
            # No configurable min/max for integers in v1
        )

        # Test invalid integer values
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"int_field": "not an int"},
            )

        # No min/max validation in v1, so 150 is valid
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"int_field": 150},
        )
        assert record.field_data["int_field"] == 150

        # NUMBER validation
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="num_field",
            field_type=FieldType.NUMBER,
            display_name="Number Field",
            # No configurable min/max for numbers in v1
        )

        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"num_field": "not a number"},
            )

        # TEXT validation
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="text_field",
            field_type=FieldType.TEXT,
            display_name="Text Field",
            # No configurable max_length in v1
        )

        # Text is only limited to 65535 chars in v1
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"text_field": "this text is valid"},
        )
        assert record.field_data["text_field"] == "this text is valid"

        # SELECT validation
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="select_field",
            field_type=FieldType.SELECT,
            display_name="Select Field",
            enum_options=["opt1", "opt2"],
        )

        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"select_field": "invalid_option"},
            )

        # MULTI_SELECT validation
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="multi_field",
            field_type=FieldType.MULTI_SELECT,
            display_name="Multi Select Field",
            enum_options=["tag1", "tag2", "tag3"],
        )

        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"multi_field": ["tag1", "invalid_tag"]},
            )

        # ARRAY validation
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="array_field",
            field_type=FieldType.ARRAY_INTEGER,
            display_name="Array Field",
            # No configurable max_items in v1
        )

        # No max_items limit in v1, so any array size is valid
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"array_field": [1, 2, 3, 4]},
        )
        assert record.field_data["array_field"] == [1, 2, 3, 4]

    async def test_nullable_fields_v1(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that all fields are nullable in v1."""
        entity = await admin_entities_service.create_entity_type(
            name="nullable_test",
            display_name="Nullable Test",
        )

        # Create fields of various types
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="text",
            field_type=FieldType.TEXT,
            display_name="Text Field",
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="integer",
            field_type=FieldType.INTEGER,
            display_name="Integer Field",
        )

        # Create record with null values (omitted fields)
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={},  # No fields provided
        )

        # Verify record was created with no field data
        assert record.field_data == {}

        # Create record with explicit None values
        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "text": None,
                "integer": None,
            },
        )

        # Verify None values are accepted
        assert record2.field_data.get("text") is None
        assert record2.field_data.get("integer") is None

        # Verify is_required is always False in v1
        fields = await admin_entities_service.list_fields(entity_id=entity.id)
        for field in fields:
            assert field.is_required is False

    async def test_field_settings_validation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that field settings have been simplified - no min/max validation in v1."""
        entity = await admin_entities_service.create_entity_type(
            name="settings_test",
            display_name="Settings Test",
        )

        # Create field - no configurable settings in v1
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="age",
            field_type=FieldType.INTEGER,
            display_name="Age",
        )

        # All integer values are valid in v1 (no min/max)
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"age": 10},
        )
        assert record1.field_data["age"] == 10

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"age": 100},
        )
        assert record2.field_data["age"] == 100

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"age": 25},
        )
        assert record3.field_data["age"] == 25

    async def test_cascade_delete_entity(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that deleting entity cascades to fields and records."""
        # Create entity with fields and records
        entity = await admin_entities_service.create_entity_type(
            name="cascade_test",
            display_name="Cascade Test",
        )
        entity_id = entity.id

        field = await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
        )
        field_id = field.id

        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"test_field": "test data"},
        )
        record_id = record.id

        # Delete the entity
        await session.delete(entity)
        await session.commit()

        # Verify entity is deleted
        stmt = select(EntityMetadata).where(EntityMetadata.id == entity_id)
        result = await session.exec(stmt)
        assert result.first() is None

        # Verify fields were cascade deleted
        stmt = select(FieldMetadata).where(FieldMetadata.id == field_id)
        result = await session.exec(stmt)
        assert result.first() is None

        # Verify records were cascade deleted
        stmt = select(EntityData).where(EntityData.id == record_id)
        result = await session.exec(stmt)
        assert result.first() is None

    async def test_workspace_isolation(
        self, session: AsyncSession, svc_admin_role: Role
    ) -> None:
        """Test that entities are isolated by workspace."""
        # Create service for workspace 1
        service1 = CustomEntitiesService(session=session, role=svc_admin_role)

        # Create entity in workspace 1
        entity1 = await service1.create_entity_type(
            name="workspace1_entity",
            display_name="Workspace 1 Entity",
        )

        # Create a different workspace role
        workspace2_id = uuid.uuid4()
        role2 = Role(
            type="service",
            user_id=svc_admin_role.user_id,
            workspace_id=workspace2_id,
            service_id="tracecat-service",
            access_level=svc_admin_role.access_level,
        )
        service2 = CustomEntitiesService(session=session, role=role2)

        # Try to access entity1 from workspace2 - should not find it
        with pytest.raises(TracecatNotFoundError):
            await service2.get_entity_type(entity1.id)

        # Create entity with same name in workspace2 - should succeed
        entity2 = await service2.create_entity_type(
            name="workspace1_entity",  # Same name as workspace1
            display_name="Workspace 2 Entity",
        )
        assert entity2.owner_id == workspace2_id
        assert entity2.owner_id != entity1.owner_id

        # Each workspace can only see its own entities
        ws1_entities = await service1.list_entity_types()
        ws2_entities = await service2.list_entity_types()

        ws1_ids = {e.id for e in ws1_entities}
        ws2_ids = {e.id for e in ws2_entities}

        assert entity1.id in ws1_ids
        assert entity1.id not in ws2_ids
        assert entity2.id in ws2_ids
        assert entity2.id not in ws1_ids

    async def test_unique_constraints(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test unique constraints for entity names and field keys."""
        # Create entity
        entity = await admin_entities_service.create_entity_type(
            name="unique_test",
            display_name="Unique Test",
        )

        # Try to create another entity with same name - should fail
        with pytest.raises(ValueError, match="already exists"):
            await admin_entities_service.create_entity_type(
                name="unique_test",  # Duplicate name
                display_name="Another Unique Test",
            )

        # Create field
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="unique_field",
            field_type=FieldType.TEXT,
            display_name="Unique Field",
        )

        # Try to create another field with same key - should fail
        with pytest.raises(ValueError, match="already exists"):
            await admin_entities_service.create_field(
                entity_id=entity.id,
                field_key="unique_field",  # Duplicate key
                field_type=FieldType.INTEGER,
                display_name="Another Unique Field",
            )


@pytest.mark.anyio
class TestRequiredFieldConstraint:
    """Test required field validation."""

    async def test_required_field_validation_on_create(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test that required fields are enforced on record creation."""
        # Create required field
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="required_name",
            field_type=FieldType.TEXT,
            display_name="Required Name",
            is_required=True,
        )

        # Try to create record without required field
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={"other_field": "value"}
            )

        # Try to create record with null value for required field
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={"required_name": None}
            )

        # Create with required field should succeed
        record = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"required_name": "John"}
        )
        assert record.field_data["required_name"] == "John"

    async def test_required_field_validation_on_update(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test that required fields cannot be set to null on update."""
        # Create required field
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="required_email",
            field_type=FieldType.TEXT,
            display_name="Required Email",
            is_required=True,
        )

        # Create record with required field
        record = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"required_email": "test@example.com"}
        )

        # Try to update to null - should fail
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.update_record(
                record_id=record.id, updates={"required_email": None}
            )

        # Update with valid value should succeed
        updated = await admin_entities_service.update_record(
            record_id=record.id, updates={"required_email": "new@example.com"}
        )
        assert updated.field_data["required_email"] == "new@example.com"

    async def test_enable_required_on_existing_data(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test enabling required constraint on field with existing null data."""
        # Create optional field
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="optional_field",
            field_type=FieldType.TEXT,
            display_name="Optional Field",
            is_required=False,
        )

        # Create records, some without the field
        await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"optional_field": "value"}
        )
        record2 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={}
        )

        # Try to enable required constraint - should fail
        with pytest.raises(ValueError, match="Cannot enable required constraint"):
            await admin_entities_service.update_field(
                field_id=field.id, is_required=True
            )

        # Update record2 to have a value
        await admin_entities_service.update_record(
            record_id=record2.id, updates={"optional_field": "value2"}
        )

        # Now enabling required should succeed
        updated_field = await admin_entities_service.update_field(
            field_id=field.id, is_required=True
        )
        assert updated_field.is_required is True


@pytest.mark.anyio
class TestUniqueFieldConstraint:
    """Test unique field validation."""

    async def test_unique_field_validation_on_create(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test that unique fields are enforced on record creation."""
        # Create unique field
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="unique_email",
            field_type=FieldType.TEXT,
            display_name="Unique Email",
            is_unique=True,
        )

        # Create first record
        await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_email": "test@example.com"}
        )

        # Try to create duplicate
        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={"unique_email": "test@example.com"}
            )

        # Different value should succeed
        record2 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_email": "other@example.com"}
        )
        assert record2.field_data["unique_email"] == "other@example.com"

        # Null values should not violate unique constraint
        record3 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_email": None}
        )
        record4 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_email": None}
        )
        assert record3.id != record4.id

    async def test_unique_field_validation_on_update(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test that unique fields are enforced on record update."""
        # Create unique field
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="unique_username",
            field_type=FieldType.TEXT,
            display_name="Unique Username",
            is_unique=True,
        )

        # Create two records
        record1 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_username": "user1"}
        )
        record2 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_username": "user2"}
        )

        # Try to update record2 to have same value as record1
        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.update_record(
                record_id=record2.id, updates={"unique_username": "user1"}
            )

        # Updating to same value should succeed (idempotent)
        updated = await admin_entities_service.update_record(
            record_id=record1.id, updates={"unique_username": "user1"}
        )
        assert updated.field_data["unique_username"] == "user1"

    async def test_enable_unique_on_existing_data(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test enabling unique constraint on field with existing duplicate data."""
        # Create non-unique field
        field = await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="non_unique_field",
            field_type=FieldType.TEXT,
            display_name="Non-Unique Field",
            is_unique=False,
        )

        # Create records with duplicate values
        await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"non_unique_field": "duplicate"}
        )
        record2 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"non_unique_field": "duplicate"}
        )

        # Try to enable unique constraint - should fail
        with pytest.raises(ValueError, match="Cannot enable unique constraint"):
            await admin_entities_service.update_field(field_id=field.id, is_unique=True)

        # Update record2 to have different value
        await admin_entities_service.update_record(
            record_id=record2.id, updates={"non_unique_field": "unique_value"}
        )

        # Now enabling unique should succeed
        updated_field = await admin_entities_service.update_field(
            field_id=field.id, is_unique=True
        )
        assert updated_field.is_unique is True

    async def test_unique_constraint_on_different_field_types(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test unique constraint works with different field types."""
        # Test with INTEGER
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="unique_id",
            field_type=FieldType.INTEGER,
            display_name="Unique ID",
            is_unique=True,
        )

        await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_id": 123}
        )

        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={"unique_id": 123}
            )

        # Test with SELECT
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="unique_status",
            field_type=FieldType.SELECT,
            display_name="Unique Status",
            enum_options=["active", "inactive", "pending"],
            is_unique=True,
        )

        await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"unique_status": "active", "unique_id": 456}
        )

        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id,
                data={"unique_status": "active", "unique_id": 789},
            )


@pytest.mark.anyio
class TestCombinedConstraints:
    """Test fields with both required and unique constraints."""

    async def test_required_and_unique_field(
        self, admin_entities_service: CustomEntitiesService, test_entity: EntityMetadata
    ):
        """Test field that is both required and unique."""
        # Create field with both constraints
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="employee_id",
            field_type=FieldType.TEXT,
            display_name="Employee ID",
            is_required=True,
            is_unique=True,
        )

        # Cannot create without the field
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={}
            )

        # Cannot create with null
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={"employee_id": None}
            )

        # Create first record
        await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"employee_id": "EMP001"}
        )

        # Cannot create duplicate
        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.create_record(
                entity_id=test_entity.id, data={"employee_id": "EMP001"}
            )

        # Create with different value succeeds
        record2 = await admin_entities_service.create_record(
            entity_id=test_entity.id, data={"employee_id": "EMP002"}
        )

        # Cannot update to null
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.update_record(
                record_id=record2.id, updates={"employee_id": None}
            )

        # Cannot update to duplicate
        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.update_record(
                record_id=record2.id, updates={"employee_id": "EMP001"}
            )


@pytest.mark.anyio
class TestRecordCreationValidation:
    """Test that record creation properly validates constraints."""

    async def test_create_record_validates_required_fields(
        self, admin_entities_service: CustomEntitiesService
    ):
        """Test that create_record properly validates required fields."""
        # Create entity with required field
        entity = await admin_entities_service.create_entity_type(
            name="test_entity", display_name="Test Entity"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="required_field",
            field_type=FieldType.TEXT,
            display_name="Required Field",
            is_required=True,
        )

        # Try to create record without required field
        with pytest.raises(TracecatValidationError, match="Required field"):
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"other_field": "value"}
            )

        # Create with required field should work
        record = await admin_entities_service.create_record(
            entity_id=entity.id, data={"required_field": "value"}
        )
        assert record.field_data["required_field"] == "value"

    async def test_create_record_validates_unique_fields(
        self, admin_entities_service: CustomEntitiesService
    ):
        """Test that create_record properly validates unique fields."""
        # Create entity with unique field
        entity = await admin_entities_service.create_entity_type(
            name="test_entity", display_name="Test Entity"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="unique_field",
            field_type=FieldType.TEXT,
            display_name="Unique Field",
            is_unique=True,
        )

        # Create first record
        await admin_entities_service.create_record(
            entity_id=entity.id, data={"unique_field": "unique_value"}
        )

        # Try to create duplicate
        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"unique_field": "unique_value"}
            )

    async def test_update_record_validates_constraints(
        self, admin_entities_service: CustomEntitiesService
    ):
        """Test that update_record properly validates constraints with record_id."""
        # Create entity with unique field
        entity = await admin_entities_service.create_entity_type(
            name="test_entity", display_name="Test Entity"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="unique_code",
            field_type=FieldType.TEXT,
            display_name="Unique Code",
            is_unique=True,
        )

        # Create two records
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"unique_code": "CODE001"}
        )
        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"unique_code": "CODE002"}
        )

        # Update record1 to same value (should work - idempotent)
        updated = await admin_entities_service.update_record(
            record_id=record1.id, updates={"unique_code": "CODE001"}
        )
        assert updated.field_data["unique_code"] == "CODE001"

        # Try to update record2 to record1's value (should fail)
        with pytest.raises(TracecatValidationError, match="already exists"):
            await admin_entities_service.update_record(
                record_id=record2.id, updates={"unique_code": "CODE001"}
            )


@pytest.mark.anyio
class TestRelationFieldConstraints:
    """Test that relation fields properly support constraints."""

    async def test_create_relation_field_with_required_constraint(
        self, admin_entities_service: CustomEntitiesService
    ):
        """Test creating a relation field with is_required=True."""
        # Create two entity types
        parent_entity = await admin_entities_service.create_entity_type(
            name="parent_entity", display_name="Parent Entity"
        )
        child_entity = await admin_entities_service.create_entity_type(
            name="child_entity", display_name="Child Entity"
        )

        # Create a required belongs_to relation field
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=parent_entity.id,
            cascade_delete=False,
        )

        field = await admin_entities_service.create_relation_field(
            entity_id=child_entity.id,
            field_key="required_parent",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Required Parent",
            relation_settings=relation_settings,
            is_required=True,  # This should now work
        )

        # Verify the field was created with is_required=True
        assert field.is_required is True
        assert field.field_key == "required_parent"
        assert field.field_type == FieldType.RELATION_BELONGS_TO

    async def test_create_relation_field_with_unique_constraint(
        self, admin_entities_service: CustomEntitiesService
    ):
        """Test creating a relation field with is_unique=True for one-to-one."""
        # Create two entity types
        entity_a = await admin_entities_service.create_entity_type(
            name="entity_a", display_name="Entity A"
        )
        entity_b = await admin_entities_service.create_entity_type(
            name="entity_b", display_name="Entity B"
        )

        # Create a unique belongs_to relation field (one-to-one)
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=entity_b.id,
            cascade_delete=False,
        )

        field = await admin_entities_service.create_relation_field(
            entity_id=entity_a.id,
            field_key="unique_link",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Unique Link",
            relation_settings=relation_settings,
            is_unique=True,  # This should now work
        )

        # Verify the field was created with is_unique=True
        assert field.is_unique is True
        assert field.field_key == "unique_link"

    async def test_update_belongs_to_with_constraints(
        self, admin_entities_service: CustomEntitiesService
    ):
        """Test that belongs_to relation respects required and unique constraints."""
        # Create entities
        parent_entity = await admin_entities_service.create_entity_type(
            name="parent", display_name="Parent"
        )
        child_entity = await admin_entities_service.create_entity_type(
            name="child", display_name="Child"
        )

        # Create required and unique belongs_to field
        relation_settings = RelationSettings(
            relation_type=RelationType.BELONGS_TO,
            target_entity_id=parent_entity.id,
            cascade_delete=False,
        )

        field = await admin_entities_service.create_relation_field(
            entity_id=child_entity.id,
            field_key="parent_ref",
            field_type=FieldType.RELATION_BELONGS_TO,
            display_name="Parent Reference",
            relation_settings=relation_settings,
            is_required=True,
            is_unique=True,
        )

        # Create records
        parent1 = await admin_entities_service.create_record(
            entity_id=parent_entity.id, data={"name": "Parent 1"}
        )
        child1 = await admin_entities_service.create_record(
            entity_id=child_entity.id, data={"name": "Child 1"}
        )
        child2 = await admin_entities_service.create_record(
            entity_id=child_entity.id, data={"name": "Child 2"}
        )

        # Set the relation for child1
        await admin_entities_service.update_belongs_to_relation(
            source_record_id=child1.id,
            field=field,
            target_record_id=parent1.id,
        )

        # Try to clear required relation - should fail
        with pytest.raises(ValueError, match="Cannot clear required relation"):
            await admin_entities_service.update_belongs_to_relation(
                source_record_id=child1.id,
                field=field,
                target_record_id=None,
            )

        # Try to link child2 to same parent (violates unique) - should fail
        with pytest.raises(ValueError, match="unique constraint"):
            await admin_entities_service.update_belongs_to_relation(
                source_record_id=child2.id,
                field=field,
                target_record_id=parent1.id,
            )
