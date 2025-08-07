import uuid
from unittest.mock import patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import EntityMetadata, FieldMetadata
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
                "Entity name must be alphanumeric with underscores only",
            ),  # spaces before lowercase check
            (
                "test entity",
                "Entity name must be alphanumeric with underscores only",
            ),  # spaces
            (
                "test-entity",
                "Entity name must be alphanumeric with underscores only",
            ),  # hyphens
            (
                "123test",
                "Entity name must start with a letter",
            ),  # must start with letter
            (
                "test@entity",
                "Entity name must be alphanumeric with underscores only",
            ),  # special chars
            ("_test", "Entity name must start with a letter"),  # must start with letter
            ("", "Entity name cannot be empty"),  # empty string
            (
                "TestEntity",
                "Entity name must be lowercase",
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
        """Test that creating field with nested structure settings is rejected."""
        # Create an entity type
        entity = EntityMetadata(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        # Try to create field with nested structure settings
        with pytest.raises(ValueError, match="Nested structures not supported in v1"):
            await admin_entities_service.create_field(
                entity_id=entity.id,
                field_key="test_field",
                field_type=FieldType.TEXT,
                display_name="Test Field",
                settings={"allow_nested": True},
            )

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
            field_settings={"max_length": 100},
            is_active=True,
            is_required=False,
        )
        session.add(field)
        await session.commit()

        # Update only display properties
        updated = await admin_entities_service.update_field_display(
            field_id=field.id,
            display_name="Updated Display Name",
            description="Updated Description",
            settings={"max_length": 200},
        )

        # Verify display properties were updated
        assert updated.display_name == "Updated Display Name"
        assert updated.description == "Updated Description"
        assert updated.field_settings["max_length"] == 200

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
        with pytest.raises(TracecatValidationError, match="Expected string, got dict"):
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
