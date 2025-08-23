import uuid
from datetime import date, datetime
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Entity, FieldMetadata, Record
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
async def test_entity(admin_entities_service: CustomEntitiesService) -> Entity:
    """Create a test entity type."""
    return await admin_entities_service.create_entity(
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
    }


@pytest.mark.anyio
class TestDefaultValues:
    """Tests for field default value functionality."""

    async def test_create_field_with_text_default(
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
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
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
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
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
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
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
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
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
    ) -> None:
        """Test that non-primitive field types cannot have default values."""
        with pytest.raises(ValueError, match="does not support default values"):
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
        test_entity: Entity,
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

    async def test_field_with_default_value(
        self,
        entities_service: CustomEntitiesService,
        admin_entities_service: CustomEntitiesService,
        test_entity: Entity,
    ) -> None:
        """Test that fields with defaults apply the default value when not provided."""
        # Create a field with a default
        await admin_entities_service.create_field(
            entity_id=test_entity.id,
            field_key="field_with_default",
            field_type=FieldType.TEXT,
            display_name="Field with Default",
            default_value="Default value",
        )

        # Should be able to create a record without providing the field
        record = await entities_service.create_record(
            entity_id=test_entity.id,
            data={},  # No fields provided
        )

        assert record.field_data["field_with_default"] == "Default value"

    async def test_update_field_default_value(
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
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
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
    ) -> None:
        """Test that SELECT field default value must be one of the options."""
        with pytest.raises(ValueError, match="not in allowed options"):
            await admin_entities_service.create_field(
                entity_id=test_entity.id,
                field_key="select_field",
                field_type=FieldType.SELECT,
                display_name="Select Field",
                enum_options=["option1", "option2", "option3"],
                default_value="invalid_option",
            )

    async def test_multi_select_default_validates_against_options(
        self, admin_entities_service: CustomEntitiesService, test_entity: Entity
    ) -> None:
        """Test that MULTI_SELECT field default values must be from the options."""
        with pytest.raises(ValueError, match="not in allowed options"):
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
                await admin_entities_service.create_entity(
                    name=invalid_name,
                    display_name="Display Name",
                    description="Description",
                )

    async def test_create_field_invalid_key(
        self, admin_entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test creating field with invalid key format."""
        # First create an entity type
        entity = Entity(
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
        entity = Entity(
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
        entity = Entity(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Original Display Name",
            description="Original Description",
            # field_settings removed - using dedicated columns now
            is_active=True,
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
        entity = Entity(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=False,  # Already inactive
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
        entity = Entity(
            owner_id=admin_entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=True,  # Already active
        )
        session.add(field)
        await session.commit()

        # Try to reactivate already active field
        with pytest.raises(ValueError, match="Field is already active"):
            await admin_entities_service.reactivate_field(field.id)

    async def test_create_record_with_nested_structure(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that the field_data column supports nested structures within proper field types."""
        # Create entity and field
        entity = Entity(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=True,
        )
        session.add(field)
        await session.commit()

        # TEXT fields must still contain strings
        record = await entities_service.create_record(
            entity_id=entity.id,
            data={"test_field": "text value"},
        )
        assert record.field_data["test_field"] == "text value"

        # Objects in TEXT fields should fail
        with pytest.raises(TracecatValidationError, match="Expected string"):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"test_field": {"nested": "object"}},  # Not allowed for TEXT field
            )

    async def test_validate_record_data_with_inactive_fields(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that inactive fields are ignored during validation."""
        # Create entity with active and inactive fields
        entity = Entity(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        active_field = FieldMetadata(
            entity_id=entity.id,
            field_key="active_field",
            field_type=FieldType.TEXT,
            display_name="Active Field",
            is_active=True,
        )
        inactive_field = FieldMetadata(
            entity_id=entity.id,
            field_key="inactive_field",
            field_type=FieldType.TEXT,
            display_name="Inactive Field",
            is_active=False,  # Inactive
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
            TracecatNotFoundError, match=f"Entity {non_existent_id} not found"
        ):
            await entities_service.get_entity(non_existent_id)

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
        entity = Entity(
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
                entity_id=uuid.uuid4(),
                field_key="text_field",
                field_type=FieldType.TEXT,
                display_name="Text Field",
                description="A text field",
                is_active=True,
            ),
            FieldMetadata(
                entity_id=uuid.uuid4(),
                field_key="number_field",
                field_type=FieldType.NUMBER,
                display_name="Number Field",
                description="A number field",
                is_active=True,
            ),
            FieldMetadata(
                entity_id=uuid.uuid4(),
                field_key="inactive_field",
                field_type=FieldType.TEXT,
                display_name="Inactive Field",
                is_active=False,  # Inactive - should be excluded
            ),
        ]

        # Generate model
        model = entities_service.get_active_fields_model(fields)

        # Verify model has correct fields
        assert "text_field" in model.model_fields
        assert "number_field" in model.model_fields
        assert "inactive_field" not in model.model_fields  # Excluded

        with pytest.raises(ValidationError):  # Pydantic will raise validation error
            model(text_field="test", unknown_field="should fail")

    async def test_admin_access_required_for_create_entity(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test that creating entity type requires admin access."""
        # entities_service has basic role, should fail
        with pytest.raises(TracecatAuthorizationError):
            await entities_service.create_entity(
                name="test_entity",
                display_name="Test Entity",
                description="Should fail",
            )

    async def test_admin_access_required_for_create_field(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that creating field requires admin access."""
        # Create entity first (bypass service for setup)
        entity = Entity(
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
        entity = Entity(
            owner_id=entities_service.workspace_id,
            name="test_entity",
            display_name="Test Entity",
            is_active=True,
        )
        session.add(entity)
        await session.commit()

        field = FieldMetadata(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
            is_active=True,
        )
        session.add(field)
        await session.commit()

        # entities_service has basic role, should fail
        with pytest.raises(TracecatAuthorizationError):
            await entities_service.deactivate_field(field.id)


@pytest.mark.anyio
class TestRelationFieldCreation:
    """Test relation field handling during record creation."""

    async def test_create_record_with_one_to_one_relation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating a record with a one_to_one relation."""
        # Create two entity types
        user_entity = await admin_entities_service.create_entity(
            name="user", display_name="User"
        )
        post_entity = await admin_entities_service.create_entity(
            name="post", display_name="Post"
        )

        # Create a user record to reference
        await admin_entities_service.create_field(
            entity_id=user_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        user_record = await admin_entities_service.create_record(
            entity_id=user_entity.id, data={"name": "John Doe"}
        )

        # Create post entity with author field (one_to_one user)
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        author_field = await admin_entities_service.create_relation_field(
            entity_id=post_entity.id,
            field_key="author",
            field_type=FieldType.RELATION_ONE_TO_ONE,
            display_name="Author",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=user_entity.id,
            ),
        )

        await admin_entities_service.create_field(
            entity_id=post_entity.id,
            field_key="title",
            field_type=FieldType.TEXT,
            display_name="Title",
        )

        # Create post with author relation
        post_record = await admin_entities_service.create_record(
            entity_id=post_entity.id,
            data={"title": "My First Post", "author": str(user_record.id)},
        )

        # Verify the record was created with proper data
        assert post_record.field_data["title"] == "My First Post"
        # Relation fields are no longer cached in field_data (stored only in RecordRelationLink)
        assert "author" not in post_record.field_data

        # Verify EntityRelationLink was created
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == post_record.id,
            RecordRelationLink.source_field_id == author_field.id,
            RecordRelationLink.target_record_id == user_record.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        link = result.first()
        assert link is not None
        assert link.source_entity_id == post_entity.id
        assert link.target_entity_id == user_entity.id

    async def test_auto_backref_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Creating a relation auto-creates complementary backref field on target."""
        # Create two entities
        a = await admin_entities_service.create_entity(name="a_ent", display_name="A")
        b = await admin_entities_service.create_entity(name="b_ent", display_name="B")

        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        # Create relation (backref is auto-created)
        a_field = await admin_entities_service.create_relation_field(
            entity_id=a.id,
            field_key="to_b",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="To B",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=b.id
            ),
        )

        # Verify complementary field exists on B and fields are linked via backref_field_id
        a_fields = await admin_entities_service.list_fields(a.id)
        b_fields = await admin_entities_service.list_fields(b.id)
        # Re-fetch a_field from listing to include backref linkage
        a_field = next(f for f in a_fields if f.field_key == "to_b")
        # Identify backref by linkage rather than assuming a fixed key
        b_field = next(f for f in b_fields if f.backref_field_id == a_field.id)
        assert a_field.backref_field_id == b_field.id
        assert b_field.backref_field_id == a_field.id
        # Check complement type and target
        assert b_field.field_type in (
            FieldType.RELATION_ONE_TO_ONE,
            FieldType.RELATION_ONE_TO_MANY,
            FieldType.RELATION_MANY_TO_ONE,
            FieldType.RELATION_MANY_TO_MANY,
        )
        # MANY_TO_ONE on source should complement to ONE_TO_MANY on target
        assert b_field.field_type == FieldType.RELATION_ONE_TO_MANY.value
        assert b_field.target_entity_id == a.id

    async def test_create_record_with_one_to_many_relation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating a record with a one_to_many relation."""
        # Create two entity types
        category_entity = await admin_entities_service.create_entity(
            name="category", display_name="Category"
        )
        product_entity = await admin_entities_service.create_entity(
            name="product", display_name="Product"
        )

        # Create product records to reference
        await admin_entities_service.create_field(
            entity_id=product_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        product1 = await admin_entities_service.create_record(
            entity_id=product_entity.id, data={"name": "Product 1"}
        )
        product2 = await admin_entities_service.create_record(
            entity_id=product_entity.id, data={"name": "Product 2"}
        )
        product3 = await admin_entities_service.create_record(
            entity_id=product_entity.id, data={"name": "Product 3"}
        )

        # Create category entity with products field (one_to_many products)
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        products_field = await admin_entities_service.create_relation_field(
            entity_id=category_entity.id,
            field_key="products",
            field_type=FieldType.RELATION_ONE_TO_MANY,
            display_name="Products",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_MANY,
                target_entity_id=product_entity.id,
            ),
        )

        await admin_entities_service.create_field(
            entity_id=category_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )

        # Create category with products relation
        category_record = await admin_entities_service.create_record(
            entity_id=category_entity.id,
            data={
                "name": "Electronics",
                "products": [str(product1.id), str(product2.id)],
            },
        )

        # Verify the record was created with proper data
        assert category_record.field_data["name"] == "Electronics"
        # Has-many relations are not cached in field_data
        assert "products" not in category_record.field_data

        # Verify EntityRelationLinks were created
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == category_record.id,
            RecordRelationLink.source_field_id == products_field.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        links = list(result.all())
        assert len(links) == 2

        target_ids = {link.target_record_id for link in links}
        assert product1.id in target_ids
        assert product2.id in target_ids
        assert product3.id not in target_ids

    async def test_create_record_with_many_to_one_relation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating a record with a many_to_one relation (single target per source)."""
        # Create two entity types
        child_entity = await admin_entities_service.create_entity(
            name="child", display_name="Child"
        )
        parent_entity = await admin_entities_service.create_entity(
            name="parent", display_name="Parent"
        )

        # Create a parent record to reference
        await admin_entities_service.create_field(
            entity_id=parent_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        parent_record = await admin_entities_service.create_record(
            entity_id=parent_entity.id, data={"name": "P1"}
        )

        # Create child entity with parent field (many_to_one parent)
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        parent_field = await admin_entities_service.create_relation_field(
            entity_id=child_entity.id,
            field_key="parent",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="Parent",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE,
                target_entity_id=parent_entity.id,
            ),
        )

        await admin_entities_service.create_field(
            entity_id=child_entity.id,
            field_key="title",
            field_type=FieldType.TEXT,
            display_name="Title",
        )

        # Create child with parent relation
        child_record = await admin_entities_service.create_record(
            entity_id=child_entity.id,
            data={"title": "C1", "parent": str(parent_record.id)},
        )

        # Verify relation not cached in field_data
        assert "parent" not in child_record.field_data

        # Verify link exists
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == child_record.id,
            RecordRelationLink.source_field_id == parent_field.id,
            RecordRelationLink.target_record_id == parent_record.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        link = result.first()
        assert link is not None

    async def test_create_record_with_many_to_many_relation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating a record with a many_to_many relation (multiple targets)."""
        # Create two entity types
        a_entity = await admin_entities_service.create_entity(
            name="a_entity", display_name="A"
        )
        b_entity = await admin_entities_service.create_entity(
            name="b_entity", display_name="B"
        )

        # Create B records
        await admin_entities_service.create_field(
            entity_id=b_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        b1 = await admin_entities_service.create_record(
            entity_id=b_entity.id, data={"name": "B1"}
        )
        b2 = await admin_entities_service.create_record(
            entity_id=b_entity.id, data={"name": "B2"}
        )

        # Create relation field on A (many_to_many to B)
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        tags_field = await admin_entities_service.create_relation_field(
            entity_id=a_entity.id,
            field_key="tags",
            field_type=FieldType.RELATION_MANY_TO_MANY,
            display_name="Tags",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_MANY,
                target_entity_id=b_entity.id,
            ),
        )

        await admin_entities_service.create_field(
            entity_id=a_entity.id,
            field_key="title",
            field_type=FieldType.TEXT,
            display_name="Title",
        )

        # Create A with tags relation
        a_record = await admin_entities_service.create_record(
            entity_id=a_entity.id,
            data={"title": "A1", "tags": [str(b1.id), str(b2.id)]},
        )

        # Verify not cached in field_data
        assert "tags" not in a_record.field_data

        # Verify links
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == a_record.id,
            RecordRelationLink.source_field_id == tags_field.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        links = list(result.all())
        assert {link.target_record_id for link in links} == {b1.id, b2.id}

    async def test_create_record_with_invalid_relation_uuid(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that invalid UUIDs in relation fields are rejected."""
        # Create entity types
        entity1 = await admin_entities_service.create_entity(
            name="entity1", display_name="Entity 1"
        )
        entity2 = await admin_entities_service.create_entity(
            name="entity2", display_name="Entity 2"
        )

        # Create relation field
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        await admin_entities_service.create_relation_field(
            entity_id=entity1.id,
            field_key="related",
            field_type=FieldType.RELATION_ONE_TO_ONE,
            display_name="Related",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=entity2.id,
            ),
        )

        # Try to create record with invalid UUID
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity1.id,
                data={"related": "not-a-uuid"},
            )
        assert "Invalid UUID format" in str(exc_info.value)

        # Try with wrong type for one_to_many
        await admin_entities_service.create_relation_field(
            entity_id=entity1.id,
            field_key="many_related",
            field_type=FieldType.RELATION_ONE_TO_MANY,
            display_name="Many Related",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_MANY,
                target_entity_id=entity2.id,
            ),
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity1.id,
                data={"many_related": "not-a-list"},  # Should be a list
            )
        assert "Expected list for one_to_many relation" in str(exc_info.value)

        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity1.id,
                data={"many_related": ["valid-uuid", "not-a-uuid"]},
            )
        assert "Invalid UUID format" in str(exc_info.value)

    async def test_create_record_ignores_relation_defaults(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that relation fields don't get default values applied."""
        # Create entity types
        entity1 = await admin_entities_service.create_entity(
            name="entity_with_relations", display_name="Entity With Relations"
        )
        entity2 = await admin_entities_service.create_entity(
            name="target_entity", display_name="Target Entity"
        )

        # Create fields with defaults
        await admin_entities_service.create_field(
            entity_id=entity1.id,
            field_key="text_field",
            field_type=FieldType.TEXT,
            display_name="Text Field",
            default_value="default text",
        )

        # Create relation field - should not support defaults
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        await admin_entities_service.create_relation_field(
            entity_id=entity1.id,
            field_key="relation_field",
            field_type=FieldType.RELATION_ONE_TO_ONE,
            display_name="Relation Field",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=entity2.id,
            ),
        )

        # Create record without providing any fields
        record = await admin_entities_service.create_record(
            entity_id=entity1.id, data={}
        )

        # Text field should get default
        assert record.field_data["text_field"] == "default text"
        # Relation field should not be in field_data (no default applied)
        assert "relation_field" not in record.field_data

    async def test_create_record_with_mixed_field_types(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating a record with both regular and relation fields."""
        # Create entity types
        company_entity = await admin_entities_service.create_entity(
            name="company", display_name="Company"
        )
        employee_entity = await admin_entities_service.create_entity(
            name="employee", display_name="Employee"
        )

        # Create employee records
        await admin_entities_service.create_field(
            entity_id=employee_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        emp1 = await admin_entities_service.create_record(
            entity_id=employee_entity.id, data={"name": "Alice"}
        )
        emp2 = await admin_entities_service.create_record(
            entity_id=employee_entity.id, data={"name": "Bob"}
        )

        # Create company with mixed fields
        await admin_entities_service.create_field(
            entity_id=company_entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Company Name",
        )
        await admin_entities_service.create_field(
            entity_id=company_entity.id,
            field_key="founded_year",
            field_type=FieldType.INTEGER,
            display_name="Founded Year",
        )

        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        ceo_field = await admin_entities_service.create_relation_field(
            entity_id=company_entity.id,
            field_key="ceo",
            field_type=FieldType.RELATION_ONE_TO_ONE,
            display_name="CEO",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_ONE,
                target_entity_id=employee_entity.id,
            ),
        )

        employees_field = await admin_entities_service.create_relation_field(
            entity_id=company_entity.id,
            field_key="employees",
            field_type=FieldType.RELATION_ONE_TO_MANY,
            display_name="Employees",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_MANY,
                target_entity_id=employee_entity.id,
            ),
        )

        # Create company with all fields
        company_record = await admin_entities_service.create_record(
            entity_id=company_entity.id,
            data={
                "name": "Tech Corp",
                "founded_year": 2020,
                "ceo": str(emp1.id),
                "employees": [str(emp1.id), str(emp2.id)],
            },
        )

        # Verify regular fields
        assert company_record.field_data["name"] == "Tech Corp"
        assert company_record.field_data["founded_year"] == 2020

        # Relations are no longer cached in field_data (stored only in RecordRelationLink)
        assert "ceo" not in company_record.field_data
        assert "employees" not in company_record.field_data

        # Verify relation links
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        # Check CEO link
        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == company_record.id,
            RecordRelationLink.source_field_id == ceo_field.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        ceo_link = result.first()
        assert ceo_link is not None
        assert ceo_link.target_record_id == emp1.id

        # Check employee links
        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == company_record.id,
            RecordRelationLink.source_field_id == employees_field.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        emp_links = list(result.all())
        assert len(emp_links) == 2

    async def test_backref_lifecycle_propagation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Deactivating/reactivating a relation field propagates to its backref."""
        # Setup entities
        src = await admin_entities_service.create_entity(
            name="src_rel", display_name="Src Rel"
        )
        dst = await admin_entities_service.create_entity(
            name="dst_rel", display_name="Dst Rel"
        )

        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        # Create relation field (source: MANY_TO_ONE → backref on target: ONE_TO_MANY)
        a_field = await admin_entities_service.create_relation_field(
            entity_id=src.id,
            field_key="to_dst",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="To Dst",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=dst.id
            ),
        )

        # Find backref field on dst
        dst_fields = await admin_entities_service.list_fields(dst.id)
        b_field = next(f for f in dst_fields if f.backref_field_id == a_field.id)

        # Deactivate source field → backref should deactivate too
        await admin_entities_service.deactivate_field(a_field.id)
        a_field_after = await admin_entities_service.get_field(a_field.id)
        b_field_after = await admin_entities_service.get_field(b_field.id)
        assert (
            a_field_after.is_active is False
            and a_field_after.deactivated_at is not None
        )
        assert (
            b_field_after.is_active is False
            and b_field_after.deactivated_at is not None
        )

        # Reactivate source field → backref should reactivate too
        await admin_entities_service.reactivate_field(a_field.id)
        a_field_after2 = await admin_entities_service.get_field(a_field.id)
        b_field_after2 = await admin_entities_service.get_field(b_field.id)
        assert (
            a_field_after2.is_active is True and a_field_after2.deactivated_at is None
        )
        assert (
            b_field_after2.is_active is True and b_field_after2.deactivated_at is None
        )

    async def test_delete_field_also_deletes_backref_and_links(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Hard deleting a relation field removes its backref and all links."""
        # Entities and fields
        src = await admin_entities_service.create_entity(
            name="src_del", display_name="Src Del"
        )
        dst = await admin_entities_service.create_entity(
            name="dst_del", display_name="Dst Del"
        )

        await admin_entities_service.create_field(
            entity_id=src.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        await admin_entities_service.create_field(
            entity_id=dst.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )

        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        rel_field = await admin_entities_service.create_relation_field(
            entity_id=src.id,
            field_key="owner",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="Owner",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=dst.id
            ),
        )

        # Resolve backref on target entity
        dst_fields = await admin_entities_service.list_fields(dst.id)
        backref_field = next(
            f for f in dst_fields if f.backref_field_id == rel_field.id
        )

        # Create records and a link via record creation
        dst_rec = await admin_entities_service.create_record(
            entity_id=dst.id, data={"name": "D"}
        )
        src_rec = await admin_entities_service.create_record(
            entity_id=src.id, data={"name": "S", "owner": str(dst_rec.id)}
        )
        # Sanity: ensure link exists
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_record_id == src_rec.id,
            RecordRelationLink.source_field_id == rel_field.id,
            RecordRelationLink.target_record_id == dst_rec.id,
        )
        result = await admin_entities_service.session.exec(stmt)
        assert result.first() is not None

        # Delete source relation field
        await admin_entities_service.delete_field(rel_field.id)

        # Backref should be gone
        from tracecat.types.exceptions import TracecatNotFoundError

        with pytest.raises(TracecatNotFoundError):
            await admin_entities_service.get_field(backref_field.id)

        # Links should be removed for both field ids
        stmt2 = select(RecordRelationLink).where(
            RecordRelationLink.source_field_id.in_([rel_field.id, backref_field.id])
        )
        result2 = await admin_entities_service.session.exec(stmt2)
        assert list(result2.all()) == []

    async def test_delete_entity_cascades_referencing_fields(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Deleting an entity cascades deletion of fields on other entities that target it."""
        # Create entities A (to be deleted), B and C referencing A
        a = await admin_entities_service.create_entity(name="a_del", display_name="A")
        b = await admin_entities_service.create_entity(name="b_del", display_name="B")
        c = await admin_entities_service.create_entity(name="c_del", display_name="C")

        # Create basic text fields
        await admin_entities_service.create_field(
            entity_id=a.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        await admin_entities_service.create_field(
            entity_id=b.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        await admin_entities_service.create_field(
            entity_id=c.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )

        # B -> A and C -> A relations
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        await admin_entities_service.create_relation_field(
            entity_id=b.id,
            field_key="to_a",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="To A",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=a.id
            ),
        )
        await admin_entities_service.create_relation_field(
            entity_id=c.id,
            field_key="to_a",
            field_type=FieldType.RELATION_ONE_TO_ONE,
            display_name="To A",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_ONE, target_entity_id=a.id
            ),
        )

        # Create records and links
        a_rec = await admin_entities_service.create_record(
            entity_id=a.id, data={"name": "A1"}
        )
        await admin_entities_service.create_record(
            entity_id=b.id, data={"name": "B1", "to_a": str(a_rec.id)}
        )
        await admin_entities_service.create_record(
            entity_id=c.id, data={"name": "C1", "to_a": str(a_rec.id)}
        )

        # Delete entity A (should cascade delete B->A and C->A fields and their backrefs)
        await admin_entities_service.delete_entity(a.id)

        # Ensure fields on B and C that targeted A are deleted
        b_fields_after = await admin_entities_service.list_fields(
            b.id, include_inactive=True
        )
        c_fields_after = await admin_entities_service.list_fields(
            c.id, include_inactive=True
        )
        assert all(f.field_key != "to_a" for f in b_fields_after)
        assert all(f.field_key != "to_a" for f in c_fields_after)

        # Ensure links pointing to A are gone
        from sqlmodel import select

        from tracecat.db.schemas import RecordRelationLink

        stmt = select(RecordRelationLink).where(
            RecordRelationLink.target_entity_id == a.id
        )
        result = await admin_entities_service.session.exec(stmt)
        assert list(result.all()) == []

    async def test_deactivate_entity_cascades_fields_and_backrefs(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Soft-deactivating an entity deactivates its fields and paired backrefs."""
        # Entities
        a = await admin_entities_service.create_entity(name="a_soft", display_name="A")
        b = await admin_entities_service.create_entity(name="b_soft", display_name="B")

        # Fields: regular and relation on A
        await admin_entities_service.create_field(
            entity_id=a.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        rel_a = await admin_entities_service.create_relation_field(
            entity_id=a.id,
            field_key="to_b",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="To B",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=b.id
            ),
        )

        # Resolve backref on B
        b_fields = await admin_entities_service.list_fields(b.id)
        backref = next(f for f in b_fields if f.backref_field_id == rel_a.id)

        # Deactivate entity A
        deactivated = await admin_entities_service.deactivate_entity(a.id)
        assert deactivated.is_active is False

        # Verify fields on A are inactive
        a_fields_after = await admin_entities_service.list_fields(
            a.id, include_inactive=True
        )
        assert all(
            not f.is_active and f.deactivated_at is not None for f in a_fields_after
        )

        # Verify backref on B is inactive
        backref_after = await admin_entities_service.get_field(backref.id)
        assert (
            backref_after.is_active is False
            and backref_after.deactivated_at is not None
        )

    async def test_reactivate_entity_cascades_fields_and_backrefs(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Reactivating an entity reactivates its fields and paired backrefs."""
        # Entities
        a = await admin_entities_service.create_entity(
            name="a_soft_re", display_name="A"
        )
        b = await admin_entities_service.create_entity(
            name="b_soft_re", display_name="B"
        )

        # Create relation
        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        rel_a = await admin_entities_service.create_relation_field(
            entity_id=a.id,
            field_key="to_b",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="To B",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=b.id
            ),
        )

        # Get backref
        b_fields = await admin_entities_service.list_fields(b.id)
        backref = next(f for f in b_fields if f.backref_field_id == rel_a.id)

        # Deactivate then reactivate entity A
        await admin_entities_service.deactivate_entity(a.id)
        reactivated = await admin_entities_service.reactivate_entity(a.id)
        assert reactivated.is_active is True

        # Verify fields on A are active
        a_fields_after = await admin_entities_service.list_fields(
            a.id, include_inactive=True
        )
        assert all(f.is_active for f in a_fields_after)

        # Verify backref on B is active again
        backref_after = await admin_entities_service.get_field(backref.id)
        assert backref_after.is_active is True and backref_after.deactivated_at is None

    async def test_cardinality_uniqueness_enforced(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """O2O and M2O enforce single target per source; O2O enforces single source per target."""
        # Setup entities
        src = await admin_entities_service.create_entity(name="src", display_name="Src")
        dst = await admin_entities_service.create_entity(name="dst", display_name="Dst")
        await admin_entities_service.create_field(
            src.id, "name", FieldType.TEXT, "Name"
        )
        await admin_entities_service.create_field(
            dst.id, "name", FieldType.TEXT, "Name"
        )

        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        # O2O field on src
        await admin_entities_service.create_relation_field(
            entity_id=src.id,
            field_key="o2o",
            field_type=FieldType.RELATION_ONE_TO_ONE,
            display_name="O2O",
            relation_settings=RelationSettings(
                relation_type=RelationType.ONE_TO_ONE, target_entity_id=dst.id
            ),
        )

        # Create records
        dst_rec = await admin_entities_service.create_record(dst.id, {"name": "d"})
        await admin_entities_service.create_record(
            src.id, {"name": "s1", "o2o": str(dst_rec.id)}
        )

        # Different source to the same target should fail for O2O (target unique)
        with pytest.raises(ValueError):
            await admin_entities_service.create_record(
                entity_id=src.id, data={"name": "s2", "o2o": str(dst_rec.id)}
            )

        # M2O: single target per source, but many sources can point to same target
        await admin_entities_service.create_relation_field(
            entity_id=src.id,
            field_key="m2o",
            field_type=FieldType.RELATION_MANY_TO_ONE,
            display_name="M2O",
            relation_settings=RelationSettings(
                relation_type=RelationType.MANY_TO_ONE, target_entity_id=dst.id
            ),
        )
        # One source points to one target at creation time
        await admin_entities_service.create_record(
            entity_id=src.id, data={"name": "s1m2o", "m2o": str(dst_rec.id)}
        )
        # Different source to same target is allowed
        await admin_entities_service.create_record(
            entity_id=src.id, data={"name": "s2m2o", "m2o": str(dst_rec.id)}
        )


@pytest.mark.anyio
class TestFieldTypeRecordCreation:
    """Comprehensive tests for record creation with all field types."""

    async def test_integer_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test INTEGER field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="integer_test", display_name="Integer Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="age",
            field_type=FieldType.INTEGER,
            display_name="Age",
        )

        # Valid integers
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"age": 42}
        )
        assert record1.field_data["age"] == 42

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"age": 0}
        )
        assert record2.field_data["age"] == 0

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"age": -100}
        )
        assert record3.field_data["age"] == -100

        # None is valid (nullable field)
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"age": None}
        )
        assert record4.field_data.get("age") is None

        # Invalid: string
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"age": "not a number"}
            )
        assert "Expected integer" in str(exc_info.value)

        # Invalid: float
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"age": 42.5}
            )
        assert "Expected integer" in str(exc_info.value)

        # Invalid: boolean (Python bool is subclass of int, but we reject it)
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"age": True}
            )
        assert "Expected integer" in str(exc_info.value)

    async def test_number_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test NUMBER field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="number_test", display_name="Number Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="price",
            field_type=FieldType.NUMBER,
            display_name="Price",
        )

        # Valid numbers
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"price": 99.99}
        )
        assert record1.field_data["price"] == 99.99

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"price": 42},  # Integer is valid for NUMBER
        )
        assert record2.field_data["price"] == 42

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"price": -123.456}
        )
        assert record3.field_data["price"] == -123.456

        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"price": 0.0}
        )
        assert record4.field_data["price"] == 0.0

        # None is valid
        record5 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"price": None}
        )
        assert record5.field_data.get("price") is None

        # Invalid: string
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"price": "not a number"}
            )
        assert "Expected number" in str(exc_info.value)

        # Invalid: boolean
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"price": False}
            )
        assert "Expected number" in str(exc_info.value)

    async def test_text_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test TEXT field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="text_test", display_name="Text Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="description",
            field_type=FieldType.TEXT,
            display_name="Description",
        )

        # Valid text values
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"description": "Normal text"}
        )
        assert record1.field_data["description"] == "Normal text"

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"description": ""},  # Empty string is valid
        )
        assert record2.field_data["description"] == ""

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"description": "Special chars: @#$%^&*()"}
        )
        assert record3.field_data["description"] == "Special chars: @#$%^&*()"

        # Long text (under 65535 limit)
        long_text = "x" * 10000
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"description": long_text}
        )
        assert record4.field_data["description"] == long_text

        # None is valid
        record5 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"description": None}
        )
        assert record5.field_data.get("description") is None

        # Invalid: text too long (>65535 characters)
        too_long_text = "x" * 65536
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"description": too_long_text}
            )
        assert "exceeds maximum 65535" in str(exc_info.value)

        # Invalid: not a string
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"description": 123}
            )
        assert "Expected string" in str(exc_info.value)

    async def test_bool_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test BOOL field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="bool_test", display_name="Bool Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="is_active",
            field_type=FieldType.BOOL,
            display_name="Is Active",
        )

        # Valid boolean values
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"is_active": True}
        )
        assert record1.field_data["is_active"] is True

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"is_active": False}
        )
        assert record2.field_data["is_active"] is False

        # None is valid
        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"is_active": None}
        )
        assert record3.field_data.get("is_active") is None

        # Invalid: string
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"is_active": "true"}
            )
        assert "Expected boolean" in str(exc_info.value)

        # Invalid: number
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"is_active": 1}
            )
        assert "Expected boolean" in str(exc_info.value)

    async def test_date_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test DATE field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="date_test", display_name="Date Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="birth_date",
            field_type=FieldType.DATE,
            display_name="Birth Date",
        )

        # Valid date values
        test_date = date(2024, 3, 15)
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"birth_date": test_date.isoformat()}
        )
        assert record1.field_data["birth_date"] == test_date.isoformat()

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"birth_date": "2023-12-31"}
        )
        assert record2.field_data["birth_date"] == "2023-12-31"

        # Date object directly
        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"birth_date": date(2022, 1, 1)}
        )
        assert record3.field_data["birth_date"] == "2022-01-01"

        # None is valid
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"birth_date": None}
        )
        assert record4.field_data.get("birth_date") is None

        # Invalid: bad format
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"birth_date": "15/03/2024"}
            )
        assert "Invalid date format" in str(exc_info.value)

        # Invalid: not a date
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"birth_date": 123}
            )
        assert "Expected date" in str(exc_info.value)

    async def test_datetime_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test DATETIME field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="datetime_test", display_name="DateTime Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="timestamp",
            field_type=FieldType.DATETIME,
            display_name="Timestamp",
        )

        # Valid datetime values
        test_dt = datetime(2024, 3, 15, 14, 30, 0)
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"timestamp": test_dt.isoformat()}
        )
        assert record1.field_data["timestamp"] == test_dt.isoformat()

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"timestamp": "2023-12-31T23:59:59"}
        )
        assert record2.field_data["timestamp"] == "2023-12-31T23:59:59"

        # With timezone
        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"timestamp": "2023-12-31T23:59:59Z"}
        )
        # Z is converted to +00:00
        assert "2023-12-31T23:59:59" in record3.field_data["timestamp"]

        # DateTime object directly
        dt_obj = datetime.now()
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"timestamp": dt_obj}
        )
        assert record4.field_data["timestamp"] == dt_obj.isoformat()

        # None is valid
        record5 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"timestamp": None}
        )
        assert record5.field_data.get("timestamp") is None

        # Invalid: bad format
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"timestamp": "not a datetime"}
            )
        assert "Invalid datetime format" in str(exc_info.value)

        # Invalid: not a datetime
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"timestamp": 123}
            )
        assert "Expected datetime" in str(exc_info.value)

    async def test_array_text_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test ARRAY_TEXT field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="array_text_test", display_name="Array Text Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="tags",
            field_type=FieldType.ARRAY_TEXT,
            display_name="Tags",
        )

        # Valid array values
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"tags": ["tag1", "tag2", "tag3"]}
        )
        assert record1.field_data["tags"] == ["tag1", "tag2", "tag3"]

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"tags": []},  # Empty array is valid
        )
        assert record2.field_data["tags"] == []

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"tags": ["single"]}
        )
        assert record3.field_data["tags"] == ["single"]

        # None is valid
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"tags": None}
        )
        assert record4.field_data.get("tags") is None

        # Invalid: not an array
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"tags": "not an array"}
            )
        assert "Expected list" in str(exc_info.value)

        # Invalid: mixed types in array
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"tags": ["string", 123, "another"]}
            )
        assert "must be strings" in str(exc_info.value)

        # Invalid: nested arrays
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"tags": [["nested", "array"]]}
            )
        assert "Nested arrays" in str(exc_info.value) or "Nested objects" in str(
            exc_info.value
        )

    async def test_array_integer_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test ARRAY_INTEGER field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="array_int_test", display_name="Array Integer Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="scores",
            field_type=FieldType.ARRAY_INTEGER,
            display_name="Scores",
        )

        # Valid integer arrays
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"scores": [100, 85, 92]}
        )
        assert record1.field_data["scores"] == [100, 85, 92]

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"scores": []},  # Empty array
        )
        assert record2.field_data["scores"] == []

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"scores": [-10, 0, 10]}
        )
        assert record3.field_data["scores"] == [-10, 0, 10]

        # None is valid
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"scores": None}
        )
        assert record4.field_data.get("scores") is None

        # Invalid: floats in integer array
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"scores": [1, 2.5, 3]}
            )
        assert "must be integers" in str(exc_info.value)

        # Invalid: booleans (even though bool is subclass of int in Python)
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"scores": [1, True, 3]}
            )
        assert "must be integers" in str(exc_info.value)

        # Invalid: strings
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"scores": [1, "2", 3]}
            )
        assert "must be integers" in str(exc_info.value)

    async def test_array_number_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test ARRAY_NUMBER field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="array_num_test", display_name="Array Number Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="measurements",
            field_type=FieldType.ARRAY_NUMBER,
            display_name="Measurements",
        )

        # Valid number arrays
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"measurements": [1.5, 2.7, 3.14]}
        )
        assert record1.field_data["measurements"] == [1.5, 2.7, 3.14]

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"measurements": [1, 2, 3]},  # Integers are valid
        )
        assert record2.field_data["measurements"] == [1, 2, 3]

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"measurements": []},  # Empty array
        )
        assert record3.field_data["measurements"] == []

        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"measurements": [-1.5, 0.0, 1.5]}
        )
        assert record4.field_data["measurements"] == [-1.5, 0.0, 1.5]

        # None is valid
        record5 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"measurements": None}
        )
        assert record5.field_data.get("measurements") is None

        # Invalid: strings in number array
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"measurements": [1.5, "2.5", 3.5]}
            )
        assert "must be numbers" in str(exc_info.value)

        # Invalid: booleans
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"measurements": [1.5, False, 3.5]}
            )
        assert "must be numbers" in str(exc_info.value)

    async def test_select_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test SELECT field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="select_test", display_name="Select Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="status",
            field_type=FieldType.SELECT,
            display_name="Status",
            enum_options=["pending", "approved", "rejected"],
        )

        # Valid selections
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"status": "pending"}
        )
        assert record1.field_data["status"] == "pending"

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"status": "approved"}
        )
        assert record2.field_data["status"] == "approved"

        # None is valid
        record3 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"status": None}
        )
        assert record3.field_data.get("status") is None

        # Invalid: not in options
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"status": "invalid"}
            )
        assert "not in allowed options" in str(exc_info.value)

        # Invalid: wrong type (not string)
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"status": ["pending"]},  # Array instead of string
            )
        assert "Expected string" in str(exc_info.value)

    async def test_multi_select_field_record_creation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test MULTI_SELECT field type validation and storage."""
        entity = await admin_entities_service.create_entity(
            name="multi_select_test", display_name="Multi Select Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="categories",
            field_type=FieldType.MULTI_SELECT,
            display_name="Categories",
            enum_options=["tech", "finance", "health", "education"],
        )

        # Valid multi-selections
        record1 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"categories": ["tech", "finance"]}
        )
        assert record1.field_data["categories"] == ["tech", "finance"]

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"categories": []},  # Empty selection
        )
        assert record2.field_data["categories"] == []

        record3 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"categories": ["health"]},  # Single selection
        )
        assert record3.field_data["categories"] == ["health"]

        # None is valid
        record4 = await admin_entities_service.create_record(
            entity_id=entity.id, data={"categories": None}
        )
        assert record4.field_data.get("categories") is None

        # Invalid: value not in options
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"categories": ["tech", "invalid"]}
            )
        assert "not in allowed options" in str(exc_info.value)

        # Invalid: not a list
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"categories": "tech"},  # String instead of list
            )
        assert "Expected list" in str(exc_info.value)


@pytest.mark.anyio
class TestFieldTypeEdgeCases:
    """Test edge cases and boundary conditions for field types."""

    async def test_create_multiple_records_all_field_types(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test creating multiple records with ALL field types to ensure consistency."""
        # Create entity with all field types
        entity = await admin_entities_service.create_entity(
            name="comprehensive_test", display_name="Comprehensive Test"
        )

        # Create all field types
        fields = [
            ("int_field", FieldType.INTEGER, None),
            ("num_field", FieldType.NUMBER, None),
            ("text_field", FieldType.TEXT, None),
            ("bool_field", FieldType.BOOL, None),
            ("date_field", FieldType.DATE, None),
            ("datetime_field", FieldType.DATETIME, None),
            ("array_text", FieldType.ARRAY_TEXT, None),
            ("array_int", FieldType.ARRAY_INTEGER, None),
            ("array_num", FieldType.ARRAY_NUMBER, None),
            ("select_field", FieldType.SELECT, ["opt1", "opt2", "opt3"]),
            ("multi_select", FieldType.MULTI_SELECT, ["tag1", "tag2", "tag3", "tag4"]),
        ]

        for field_key, field_type, options in fields:
            await admin_entities_service.create_field(
                entity_id=entity.id,
                field_key=field_key,
                field_type=field_type,
                display_name=field_key.replace("_", " ").title(),
                enum_options=options,
            )

        # Create multiple records with different values
        test_date1 = date(2024, 1, 1)
        test_dt1 = datetime(2024, 1, 1, 12, 0, 0)

        record1 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "int_field": 100,
                "num_field": 99.99,
                "text_field": "First record",
                "bool_field": True,
                "date_field": test_date1.isoformat(),
                "datetime_field": test_dt1.isoformat(),
                "array_text": ["a", "b", "c"],
                "array_int": [1, 2, 3],
                "array_num": [1.1, 2.2, 3.3],
                "select_field": "opt1",
                "multi_select": ["tag1", "tag2"],
            },
        )

        # Verify all fields stored correctly
        assert record1.field_data["int_field"] == 100
        assert record1.field_data["num_field"] == 99.99
        assert record1.field_data["text_field"] == "First record"
        assert record1.field_data["bool_field"] is True
        assert record1.field_data["date_field"] == test_date1.isoformat()
        assert record1.field_data["datetime_field"] == test_dt1.isoformat()
        assert record1.field_data["array_text"] == ["a", "b", "c"]
        assert record1.field_data["array_int"] == [1, 2, 3]
        assert record1.field_data["array_num"] == [1.1, 2.2, 3.3]
        assert record1.field_data["select_field"] == "opt1"
        assert record1.field_data["multi_select"] == ["tag1", "tag2"]

        # Create second record with different values
        test_date2 = date(2024, 12, 31)
        test_dt2 = datetime(2024, 12, 31, 23, 59, 59)

        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "int_field": -50,
                "num_field": 0.01,
                "text_field": "Second record with different text",
                "bool_field": False,
                "date_field": test_date2.isoformat(),
                "datetime_field": test_dt2.isoformat(),
                "array_text": ["x", "y", "z"],
                "array_int": [10, 20, 30],
                "array_num": [0.1, 0.2, 0.3],
                "select_field": "opt2",
                "multi_select": ["tag3", "tag4"],
            },
        )

        assert record2.field_data["int_field"] == -50
        assert record2.field_data["bool_field"] is False
        assert record2.field_data["select_field"] == "opt2"

        # Create third record with empty/minimal values
        record3 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "int_field": 0,
                "num_field": 0.0,
                "text_field": "",
                "bool_field": False,
                "date_field": "2024-06-15",
                "datetime_field": "2024-06-15T00:00:00",
                "array_text": [],
                "array_int": [],
                "array_num": [],
                "select_field": "opt3",
                "multi_select": [],
            },
        )

        assert record3.field_data["int_field"] == 0
        assert record3.field_data["text_field"] == ""
        assert record3.field_data["array_text"] == []
        assert record3.field_data["multi_select"] == []

        # Query all records and verify they're all stored
        all_records = await admin_entities_service.query_records(entity_id=entity.id)
        assert len(all_records) == 3
        record_ids = {r.id for r in all_records}
        assert record1.id in record_ids
        assert record2.id in record_ids
        assert record3.id in record_ids

    async def test_record_creation_with_null_values(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that all field types properly handle None/null values."""
        entity = await admin_entities_service.create_entity(
            name="null_test", display_name="Null Value Test"
        )

        # Create all nullable field types
        fields = [
            ("int_null", FieldType.INTEGER, None),
            ("num_null", FieldType.NUMBER, None),
            ("text_null", FieldType.TEXT, None),
            ("bool_null", FieldType.BOOL, None),
            ("date_null", FieldType.DATE, None),
            ("datetime_null", FieldType.DATETIME, None),
            ("array_text_null", FieldType.ARRAY_TEXT, None),
            ("array_int_null", FieldType.ARRAY_INTEGER, None),
            ("array_num_null", FieldType.ARRAY_NUMBER, None),
            ("select_null", FieldType.SELECT, ["opt1", "opt2"]),
            ("multi_null", FieldType.MULTI_SELECT, ["tag1", "tag2"]),
        ]

        for field_key, field_type, options in fields:
            await admin_entities_service.create_field(
                entity_id=entity.id,
                field_key=field_key,
                field_type=field_type,
                display_name=field_key,
                enum_options=options,
            )

        # Create record with all None values
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "int_null": None,
                "num_null": None,
                "text_null": None,
                "bool_null": None,
                "date_null": None,
                "datetime_null": None,
                "array_text_null": None,
                "array_int_null": None,
                "array_num_null": None,
                "select_null": None,
                "multi_null": None,
            },
        )

        # Verify all fields accepted None
        for field_key, _, _ in fields:
            assert (
                field_key in record.field_data
                or record.field_data.get(field_key) is None
            )

        # Create record with no data (implicit None)
        record2 = await admin_entities_service.create_record(
            entity_id=entity.id, data={}
        )
        assert record2.field_data == {}

    async def test_record_creation_with_empty_collections(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that empty collections are handled properly."""
        entity = await admin_entities_service.create_entity(
            name="empty_test", display_name="Empty Collections Test"
        )

        # Create collection field types
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="empty_text",
            field_type=FieldType.TEXT,
            display_name="Empty Text",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="empty_array_text",
            field_type=FieldType.ARRAY_TEXT,
            display_name="Empty Array Text",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="empty_array_int",
            field_type=FieldType.ARRAY_INTEGER,
            display_name="Empty Array Int",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="empty_array_num",
            field_type=FieldType.ARRAY_NUMBER,
            display_name="Empty Array Num",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="empty_multi",
            field_type=FieldType.MULTI_SELECT,
            display_name="Empty Multi",
            enum_options=["opt1", "opt2"],
        )

        # Create record with empty collections
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "empty_text": "",
                "empty_array_text": [],
                "empty_array_int": [],
                "empty_array_num": [],
                "empty_multi": [],
            },
        )

        assert record.field_data["empty_text"] == ""
        assert record.field_data["empty_array_text"] == []
        assert record.field_data["empty_array_int"] == []
        assert record.field_data["empty_array_num"] == []
        assert record.field_data["empty_multi"] == []

    async def test_record_creation_type_validation_strictness(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that type validation is strict and doesn't do unwanted coercion."""
        entity = await admin_entities_service.create_entity(
            name="strict_test", display_name="Strict Type Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="strict_int",
            field_type=FieldType.INTEGER,
            display_name="Strict Integer",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="strict_bool",
            field_type=FieldType.BOOL,
            display_name="Strict Bool",
        )

        # String that looks like number should be rejected for integer
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"strict_int": "123"}
            )
        assert "Expected integer" in str(exc_info.value)

        # String that looks like boolean should be rejected
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"strict_bool": "true"}
            )
        assert "Expected boolean" in str(exc_info.value)

        # 0 and 1 should not be accepted as boolean
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"strict_bool": 0}
            )
        assert "Expected boolean" in str(exc_info.value)

        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id, data={"strict_bool": 1}
            )
        assert "Expected boolean" in str(exc_info.value)

    async def test_record_creation_with_extreme_values(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test field types with extreme but valid values."""
        entity = await admin_entities_service.create_entity(
            name="extreme_test", display_name="Extreme Values Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="big_int",
            field_type=FieldType.INTEGER,
            display_name="Big Integer",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="tiny_float",
            field_type=FieldType.NUMBER,
            display_name="Tiny Float",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="long_text",
            field_type=FieldType.TEXT,
            display_name="Long Text",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="big_array",
            field_type=FieldType.ARRAY_INTEGER,
            display_name="Big Array",
        )

        # Test with extreme values
        very_long_text = "x" * 65535  # Maximum allowed
        big_array = list(range(1000))  # Large array

        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "big_int": 2147483647,  # Max 32-bit int
                "tiny_float": 0.0000000001,
                "long_text": very_long_text,
                "big_array": big_array,
            },
        )

        assert record.field_data["big_int"] == 2147483647
        assert record.field_data["tiny_float"] == 0.0000000001
        assert len(record.field_data["long_text"]) == 65535
        assert len(record.field_data["big_array"]) == 1000

    async def test_nested_structures_with_proper_field_types(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that nested structures are handled correctly based on field types."""
        entity = await admin_entities_service.create_entity(
            name="nested_test", display_name="Nested Test"
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="text_field",
            field_type=FieldType.TEXT,
            display_name="Text Field",
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="array_field",
            field_type=FieldType.ARRAY_TEXT,
            display_name="Array Field",
        )

        # TEXT fields must contain strings only
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"text_field": "plain text value"},
        )
        assert record.field_data["text_field"] == "plain text value"

        # Objects not allowed in TEXT fields
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"text_field": {"nested": "object"}},
            )
        assert "Expected string" in str(exc_info.value)

        # Arrays can contain strings
        record2 = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={"array_field": ["item1", "item2"]},
        )
        assert record2.field_data["array_field"] == ["item1", "item2"]

        # Nested arrays still rejected (regardless of field type)
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"array_field": [["nested", "array"]]},
            )
        assert "Nested arrays" in str(exc_info.value) or "excessive nesting" in str(
            exc_info.value
        )

        # Arrays cannot contain objects for ARRAY_TEXT type
        with pytest.raises(TracecatValidationError) as exc_info:
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={"array_field": [{"item": "object"}]},
            )
        assert "must be strings" in str(exc_info.value)


@pytest.mark.anyio
class TestConstraintValidationMethods:
    """Test the internal validation methods are called correctly."""

    async def test_validate_record_data_called_on_update(
        self, admin_entities_service: CustomEntitiesService, mocker
    ):
        """Test that update_record calls validation."""
        # Create entity and record
        entity = await admin_entities_service.create_entity(
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

        # Verify validate_record_data was called
        spy.assert_called()

    async def test_validate_record_data_called_on_create(
        self, admin_entities_service: CustomEntitiesService, mocker
    ):
        """Test that create_record calls validation."""
        # Create entity
        entity = await admin_entities_service.create_entity(
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

        # Verify validate_record_data was called
        spy.assert_called_once()


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
        entity = await admin_entities_service.create_entity(
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
        assert record1.entity_id == entity.id
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
        entity = await admin_entities_service.create_entity(
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
        entity = await admin_entities_service.create_entity(
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
        entity = await admin_entities_service.create_entity(
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
        stmt = select(Record).where(Record.id == record.id)
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
        entity = await admin_entities_service.create_entity(
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
        entity = await admin_entities_service.create_entity(
            name="flat_test",
            display_name="Flat Structure Test",
        )

        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="data_field",
            field_type=FieldType.TEXT,
            display_name="Data Field",
        )

        # Test that TEXT fields still require strings
        record = await admin_entities_service.create_record(
            entity_id=entity.id,
            data={
                "data_field": "text value",  # TEXT field requires string
            },
        )
        assert record.field_data["data_field"] == "text value"

        # Test nested array rejection
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="array_field",
            field_type=FieldType.ARRAY_TEXT,
            display_name="Array Field",
        )

        with pytest.raises(
            TracecatValidationError, match="Nested arrays|excessive nesting"
        ):
            await admin_entities_service.create_record(
                entity_id=entity.id,
                data={
                    "array_field": [["nested", "array"]],  # Still not allowed
                },
            )

    async def test_field_type_validation_all_types(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test validation for all field types."""
        entity = await admin_entities_service.create_entity(
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
        entity = await admin_entities_service.create_entity(
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

        # Verify fields were created without constraints
        fields = await admin_entities_service.list_fields(entity_id=entity.id)
        assert len(fields) == 2

    async def test_field_settings_validation(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        """Test that field settings have been simplified - no min/max validation in v1."""
        entity = await admin_entities_service.create_entity(
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
        entity = await admin_entities_service.create_entity(
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
        stmt = select(Entity).where(Entity.id == entity_id)
        result = await session.exec(stmt)
        assert result.first() is None

        # Verify fields were cascade deleted
        stmt = select(FieldMetadata).where(FieldMetadata.id == field_id)
        result = await session.exec(stmt)
        assert result.first() is None

        # Verify records were cascade deleted
        stmt = select(Record).where(Record.id == record_id)
        result = await session.exec(stmt)
        assert result.first() is None

    async def test_workspace_isolation(
        self, session: AsyncSession, svc_admin_role: Role
    ) -> None:
        """Test that entities are isolated by workspace."""
        # Create service for workspace 1
        service1 = CustomEntitiesService(session=session, role=svc_admin_role)

        # Create entity in workspace 1
        entity1 = await service1.create_entity(
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
            await service2.get_entity(entity1.id)

        # Create entity with same name in workspace2 - should succeed
        entity2 = await service2.create_entity(
            name="workspace1_entity",  # Same name as workspace1
            display_name="Workspace 2 Entity",
        )
        assert entity2.owner_id == workspace2_id
        assert entity2.owner_id != entity1.owner_id

        # Each workspace can only see its own entities
        ws1_entities = await service1.list_entities()
        ws2_entities = await service2.list_entities()

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
        entity = await admin_entities_service.create_entity(
            name="unique_test",
            display_name="Unique Test",
        )

        # Try to create another entity with same name - should fail
        with pytest.raises(ValueError, match="already exists"):
            await admin_entities_service.create_entity(
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
class TestInactiveEntityWriteRejection:
    async def test_create_record_rejected_when_entity_inactive(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        entity = await admin_entities_service.create_entity(
            name="inactive_create_rec", display_name="Inactive Create Rec"
        )
        # Deactivate entity
        await admin_entities_service.deactivate_entity(entity.id)

        # Attempt to create record should be rejected
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_record(entity_id=entity.id, data={})

    async def test_update_record_rejected_when_entity_inactive(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        entity = await admin_entities_service.create_entity(
            name="inactive_update_rec", display_name="Inactive Update Rec"
        )
        # Add a simple field and record
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        rec = await admin_entities_service.create_record(
            entity_id=entity.id, data={"name": "x"}
        )
        # Deactivate entity
        await admin_entities_service.deactivate_entity(entity.id)
        # Attempt to update record should be rejected
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.update_record(rec.id, {"name": "y"})

    async def test_delete_record_rejected_when_entity_inactive(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        entity = await admin_entities_service.create_entity(
            name="inactive_delete_rec", display_name="Inactive Delete Rec"
        )
        await admin_entities_service.create_field(
            entity_id=entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Name",
        )
        rec = await admin_entities_service.create_record(
            entity_id=entity.id, data={"name": "x"}
        )
        await admin_entities_service.deactivate_entity(entity.id)
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.delete_record(rec.id)

    async def test_create_field_rejected_when_entity_inactive(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        entity = await admin_entities_service.create_entity(
            name="inactive_create_field", display_name="Inactive Create Field"
        )
        await admin_entities_service.deactivate_entity(entity.id)
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_field(
                entity_id=entity.id,
                field_key="f",
                field_type=FieldType.TEXT,
                display_name="F",
            )

    async def test_create_relation_field_rejected_when_source_or_target_inactive(
        self, admin_entities_service: CustomEntitiesService
    ) -> None:
        src = await admin_entities_service.create_entity(
            name="src_inact", display_name="Src Inact"
        )
        tgt = await admin_entities_service.create_entity(
            name="tgt_inact", display_name="Tgt Inact"
        )

        from tracecat.entities.enums import RelationType
        from tracecat.entities.models import RelationSettings

        # Deactivate source - should reject
        await admin_entities_service.deactivate_entity(src.id)
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_relation_field(
                entity_id=src.id,
                field_key="rel",
                field_type=FieldType.RELATION_MANY_TO_ONE,
                display_name="Rel",
                relation_settings=RelationSettings(
                    relation_type=RelationType.MANY_TO_ONE, target_entity_id=tgt.id
                ),
            )

        # Reactivate source, deactivate target - should reject
        await admin_entities_service.reactivate_entity(src.id)
        await admin_entities_service.deactivate_entity(tgt.id)
        with pytest.raises(TracecatValidationError):
            await admin_entities_service.create_relation_field(
                entity_id=src.id,
                field_key="rel2",
                field_type=FieldType.RELATION_MANY_TO_ONE,
                display_name="Rel2",
                relation_settings=RelationSettings(
                    relation_type=RelationType.MANY_TO_ONE, target_entity_id=tgt.id
                ),
            )
