import uuid
from datetime import date, datetime

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import EntityData, EntityMetadata, FieldMetadata
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def entities_service(
    session: AsyncSession, svc_admin_role: Role
) -> CustomEntitiesService:
    """Create an entities service instance with admin role for testing."""
    return CustomEntitiesService(session=session, role=svc_admin_role)


@pytest.fixture
def entity_create_params() -> dict:
    """Sample entity type creation parameters."""
    return {
        "name": "customer",
        "display_name": "Customer",
        "description": "Customer entity for integration testing",
        "icon": "user-icon",
        "settings": {"default_view": "table"},
    }


@pytest.mark.anyio
class TestEntitiesIntegration:
    async def test_complete_entity_lifecycle(
        self, entities_service: CustomEntitiesService, entity_create_params: dict
    ) -> None:
        """Test complete lifecycle: create entity -> add fields -> create records -> query."""
        # Step 1: Create entity type
        entity = await entities_service.create_entity_type(**entity_create_params)
        assert entity.name == entity_create_params["name"]
        assert entity.display_name == entity_create_params["display_name"]
        assert entity.owner_id == entities_service.workspace_id

        # Step 2: Add fields of various types
        text_field = await entities_service.create_field(
            entity_id=entity.id,
            field_key="name",
            field_type=FieldType.TEXT,
            display_name="Customer Name",
            description="Full name of the customer",
            settings={"max_length": 255},
        )
        assert text_field.field_key == "name"
        assert text_field.field_type == FieldType.TEXT

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="age",
            field_type=FieldType.INTEGER,
            display_name="Age",
            settings={"min": 0, "max": 150},
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="status",
            field_type=FieldType.SELECT,
            display_name="Status",
            settings={"options": ["active", "inactive", "pending"]},
        )

        # Step 3: Create records
        record1 = await entities_service.create_record(
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

        record2 = await entities_service.create_record(
            entity_id=entity.id,
            data={
                "name": "Jane Smith",
                "age": 25,
                "status": "pending",
            },
        )

        # Step 4: Query records
        all_records = await entities_service.query_records(entity_id=entity.id)
        assert len(all_records) == 2
        record_ids = {r.id for r in all_records}
        assert record1.id in record_ids
        assert record2.id in record_ids

        # Step 5: Update a record
        updated_record = await entities_service.update_record(
            record_id=record1.id, updates={"status": "inactive", "age": 31}
        )
        assert updated_record.field_data["status"] == "inactive"
        assert updated_record.field_data["age"] == 31
        assert updated_record.field_data["name"] == "John Doe"  # Unchanged

        # Step 6: Delete a record
        await entities_service.delete_record(record2.id)
        remaining_records = await entities_service.query_records(entity_id=entity.id)
        assert len(remaining_records) == 1
        assert remaining_records[0].id == record1.id

    async def test_create_entity_with_multiple_field_types(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test creating entity with all supported field types."""
        # Create entity
        entity = await entities_service.create_entity_type(
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
            field = await entities_service.create_field(
                entity_id=entity.id,
                field_key=field_key,
                field_type=field_type,
                display_name=field_key.replace("_", " ").title(),
                settings=settings,
            )
            created_fields.append(field)

        # Verify all fields were created
        all_fields = await entities_service.list_fields(entity_id=entity.id)
        assert len(all_fields) == len(field_configs)

        # Create a record with all field types
        test_date = date.today()
        test_datetime = datetime.now()

        record = await entities_service.create_record(
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
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that field key and type are immutable after creation."""
        # Create entity and field
        entity = await entities_service.create_entity_type(
            name="immutable_test",
            display_name="Immutability Test",
        )

        field = await entities_service.create_field(
            entity_id=entity.id,
            field_key="original_key",
            field_type=FieldType.TEXT,
            display_name="Original Display Name",
            description="Original Description",
        )

        original_key = field.field_key
        original_type = field.field_type

        # Update display properties only
        updated = await entities_service.update_field_display(
            field_id=field.id,
            display_name="New Display Name",
            description="New Description",
            settings={"max_length": 1000},
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
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that soft-deleting fields preserves existing data."""
        # Create entity with fields
        entity = await entities_service.create_entity_type(
            name="soft_delete_test",
            display_name="Soft Delete Test",
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="keep_field",
            field_type=FieldType.TEXT,
            display_name="Keep Field",
        )

        field2 = await entities_service.create_field(
            entity_id=entity.id,
            field_key="delete_field",
            field_type=FieldType.TEXT,
            display_name="Delete Field",
        )

        # Create record with both fields
        record = await entities_service.create_record(
            entity_id=entity.id,
            data={
                "keep_field": "keep this data",
                "delete_field": "soft delete this field",
            },
        )

        # Soft delete field2
        deactivated = await entities_service.deactivate_field(field2.id)
        assert deactivated.is_active is False
        assert deactivated.deactivated_at is not None

        # Verify field data is still in database
        stmt = select(EntityData).where(EntityData.id == record.id)
        result = await session.exec(stmt)
        db_record = result.one()
        assert "delete_field" in db_record.field_data
        assert db_record.field_data["delete_field"] == "soft delete this field"

        # Try to create new record - deleted field should be ignored
        new_record = await entities_service.create_record(
            entity_id=entity.id,
            data={
                "keep_field": "new keep data",
                "delete_field": "this should be ignored",  # Inactive field
            },
        )
        assert "keep_field" in new_record.field_data
        assert "delete_field" not in new_record.field_data

        # Reactivate field
        reactivated = await entities_service.reactivate_field(field2.id)
        assert reactivated.is_active is True
        assert reactivated.deactivated_at is None

        # Now the field should work again
        record_with_reactivated = await entities_service.create_record(
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
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test that inactive fields are excluded from validation."""
        # Create entity with active and inactive fields
        entity = await entities_service.create_entity_type(
            name="validation_test",
            display_name="Validation Test",
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="active",
            field_type=FieldType.INTEGER,
            display_name="Active Field",
            settings={"min": 0, "max": 100},
        )

        inactive_field = await entities_service.create_field(
            entity_id=entity.id,
            field_key="inactive",
            field_type=FieldType.INTEGER,
            display_name="Inactive Field",
            settings={"min": 0, "max": 100},
        )

        # Deactivate the inactive field
        await entities_service.deactivate_field(inactive_field.id)

        # Create record with invalid value for inactive field (should be ignored)
        record = await entities_service.create_record(
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
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test that nested structures are rejected."""
        entity = await entities_service.create_entity_type(
            name="flat_test",
            display_name="Flat Structure Test",
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="data_field",
            field_type=FieldType.TEXT,
            display_name="Data Field",
        )

        # Test nested object rejection
        with pytest.raises(ValueError, match="Nested objects not allowed"):
            await entities_service.create_record(
                entity_id=entity.id,
                data={
                    "data_field": {"nested": "object"},  # Not allowed
                },
            )

        # Test nested array rejection
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="array_field",
            field_type=FieldType.ARRAY_TEXT,
            display_name="Array Field",
        )

        with pytest.raises(ValueError, match="Nested objects not allowed"):
            await entities_service.create_record(
                entity_id=entity.id,
                data={
                    "array_field": [["nested", "array"]],  # Not allowed
                },
            )

    async def test_field_type_validation_all_types(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test validation for all field types."""
        entity = await entities_service.create_entity_type(
            name="validation_all_types",
            display_name="Validation All Types",
        )

        # INTEGER validation
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="int_field",
            field_type=FieldType.INTEGER,
            display_name="Integer Field",
            settings={"min": 0, "max": 100},
        )

        # Test invalid integer values
        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"int_field": "not an int"},
            )

        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"int_field": 150},  # Exceeds max
            )

        # NUMBER validation
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="num_field",
            field_type=FieldType.NUMBER,
            display_name="Number Field",
            settings={"min": 0.0, "max": 10.0},
        )

        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"num_field": "not a number"},
            )

        # TEXT validation
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="text_field",
            field_type=FieldType.TEXT,
            display_name="Text Field",
            settings={"max_length": 10},
        )

        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"text_field": "this text is too long for the field"},
            )

        # SELECT validation
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="select_field",
            field_type=FieldType.SELECT,
            display_name="Select Field",
            settings={"options": ["opt1", "opt2"]},
        )

        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"select_field": "invalid_option"},
            )

        # MULTI_SELECT validation
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="multi_field",
            field_type=FieldType.MULTI_SELECT,
            display_name="Multi Select Field",
            settings={"options": ["tag1", "tag2", "tag3"]},
        )

        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"multi_field": ["tag1", "invalid_tag"]},
            )

        # ARRAY validation
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="array_field",
            field_type=FieldType.ARRAY_INTEGER,
            display_name="Array Field",
            settings={"max_items": 3},
        )

        with pytest.raises(TracecatValidationError):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"array_field": [1, 2, 3, 4]},  # Too many items
            )

    async def test_nullable_fields_v1(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test that all fields are nullable in v1."""
        entity = await entities_service.create_entity_type(
            name="nullable_test",
            display_name="Nullable Test",
        )

        # Create fields of various types
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="text",
            field_type=FieldType.TEXT,
            display_name="Text Field",
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="integer",
            field_type=FieldType.INTEGER,
            display_name="Integer Field",
        )

        # Create record with null values (omitted fields)
        record = await entities_service.create_record(
            entity_id=entity.id,
            data={},  # No fields provided
        )

        # Verify record was created with no field data
        assert record.field_data == {}

        # Create record with explicit None values
        record2 = await entities_service.create_record(
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
        fields = await entities_service.list_fields(entity_id=entity.id)
        for field in fields:
            assert field.is_required is False

    async def test_field_settings_validation(
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test field settings validation."""
        entity = await entities_service.create_entity_type(
            name="settings_test",
            display_name="Settings Test",
        )

        # Create field with min/max settings
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="age",
            field_type=FieldType.INTEGER,
            display_name="Age",
            settings={"min": 18, "max": 65},
        )

        # Test value below minimum
        with pytest.raises(TracecatValidationError, match="below minimum"):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"age": 10},
            )

        # Test value above maximum
        with pytest.raises(TracecatValidationError, match="exceeds maximum"):
            await entities_service.create_record(
                entity_id=entity.id,
                data={"age": 100},
            )

        # Valid value
        record = await entities_service.create_record(
            entity_id=entity.id,
            data={"age": 25},
        )
        assert record.field_data["age"] == 25

    async def test_cascade_delete_entity(
        self, entities_service: CustomEntitiesService, session: AsyncSession
    ) -> None:
        """Test that deleting entity cascades to fields and records."""
        # Create entity with fields and records
        entity = await entities_service.create_entity_type(
            name="cascade_test",
            display_name="Cascade Test",
        )
        entity_id = entity.id

        field = await entities_service.create_field(
            entity_id=entity.id,
            field_key="test_field",
            field_type=FieldType.TEXT,
            display_name="Test Field",
        )
        field_id = field.id

        record = await entities_service.create_record(
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
        self, entities_service: CustomEntitiesService
    ) -> None:
        """Test unique constraints for entity names and field keys."""
        # Create entity
        entity = await entities_service.create_entity_type(
            name="unique_test",
            display_name="Unique Test",
        )

        # Try to create another entity with same name - should fail
        with pytest.raises(ValueError, match="already exists"):
            await entities_service.create_entity_type(
                name="unique_test",  # Duplicate name
                display_name="Another Unique Test",
            )

        # Create field
        await entities_service.create_field(
            entity_id=entity.id,
            field_key="unique_field",
            field_type=FieldType.TEXT,
            display_name="Unique Field",
        )

        # Try to create another field with same key - should fail
        with pytest.raises(ValueError, match="already exists"):
            await entities_service.create_field(
                entity_id=entity.id,
                field_key="unique_field",  # Duplicate key
                field_type=FieldType.INTEGER,
                display_name="Another Unique Field",
            )
