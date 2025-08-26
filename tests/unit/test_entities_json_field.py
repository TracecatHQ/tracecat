"""Tests for JSON field type in custom entities."""

import pytest
from pydantic_core import PydanticCustomError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType, validate_field_value_type
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestJSONFieldValidation:
    """Test JSON field type validation."""

    def test_validate_json_field_accepts_dict(self):
        """Test that JSON field accepts dict values."""
        value = {"key": "value", "nested": {"data": 123}}
        result = validate_field_value_type(value, FieldType.JSON)
        assert result == value

    def test_validate_json_field_accepts_list(self):
        """Test that JSON field accepts list values."""
        value = [1, 2, {"item": "value"}]
        result = validate_field_value_type(value, FieldType.JSON)
        assert result == value

    def test_validate_json_field_rejects_primitives(self):
        """Test that JSON field rejects non-dict/list types."""
        with pytest.raises(PydanticCustomError, match="Expected dict or list"):
            validate_field_value_type("string", FieldType.JSON)

        with pytest.raises(PydanticCustomError, match="Expected dict or list"):
            validate_field_value_type(123, FieldType.JSON)

        with pytest.raises(PydanticCustomError, match="Expected dict or list"):
            validate_field_value_type(True, FieldType.JSON)

    def test_validate_json_field_depth_limit(self):
        """Test that JSON field enforces depth limit."""
        # 3 levels deep - should pass
        value = {"level1": {"level2": {"level3": "value"}}}
        result = validate_field_value_type(value, FieldType.JSON)
        assert result == value

        # 4 levels deep - should fail
        value = {"level1": {"level2": {"level3": {"level4": "too deep"}}}}
        with pytest.raises(PydanticCustomError, match="exceed 3 levels"):
            validate_field_value_type(value, FieldType.JSON)

    def test_validate_json_field_rejects_nested_arrays(self):
        """Test that JSON field rejects nested arrays."""
        # Array with nested array - should fail
        value = [1, 2, [3, 4]]
        with pytest.raises(PydanticCustomError, match="nested arrays"):
            validate_field_value_type(value, FieldType.JSON)

        # Array with objects - should pass
        value = [{"item": 1}, {"item": 2}]
        result = validate_field_value_type(value, FieldType.JSON)
        assert result == value

    def test_validate_json_field_allows_none(self):
        """Test that JSON field allows None values."""
        result = validate_field_value_type(None, FieldType.JSON)
        assert result is None


@pytest.mark.anyio
class TestJSONFieldService:
    """Test JSON field operations in the service layer."""

    @pytest.fixture
    async def entities_service(
        self, session: AsyncSession, svc_admin_role: Role
    ) -> CustomEntitiesService:
        """Create entities service with admin role."""
        return CustomEntitiesService(session=session, role=svc_admin_role)

    async def test_create_json_field(self, entities_service: CustomEntitiesService):
        """Test creating a JSON field."""
        # Create entity
        entity = await entities_service.create_entity(
            name="json_test_entity", display_name="JSON Test Entity"
        )

        # Create JSON field
        field = await entities_service.create_field(
            entity_id=entity.id,
            field_key="json_data",
            field_type=FieldType.JSON,
            display_name="JSON Data",
            description="Field for storing structured data",
        )

        assert field.field_key == "json_data"
        assert field.field_type == FieldType.JSON.value
        assert field.display_name == "JSON Data"
        assert field.is_active

    async def test_create_json_field_with_default_value(
        self, entities_service: CustomEntitiesService
    ):
        """Test creating a JSON field with a default value."""
        entity = await entities_service.create_entity(
            name="json_default_entity", display_name="JSON Default Entity"
        )

        default_value = {"status": "active", "count": 0}

        field = await entities_service.create_field(
            entity_id=entity.id,
            field_key="json_with_default",
            field_type=FieldType.JSON,
            display_name="JSON with Default",
            default_value=default_value,
        )

        assert field.default_value == default_value

    async def test_create_record_with_json_field(
        self, entities_service: CustomEntitiesService
    ):
        """Test creating a record with JSON field data."""
        entity = await entities_service.create_entity(
            name="json_record_entity", display_name="JSON Record Entity"
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="config",
            field_type=FieldType.JSON,
            display_name="Configuration",
        )

        # Create record with JSON data
        json_data = {
            "settings": {
                "theme": "dark",
                "notifications": {"email": True, "push": False},
            }
        }

        record = await entities_service.create_record(
            entity_id=entity.id, data={"config": json_data}
        )

        assert record.field_data["config"] == json_data

    async def test_create_record_with_json_list(
        self, entities_service: CustomEntitiesService
    ):
        """Test creating a record with JSON field containing a list."""
        entity = await entities_service.create_entity(
            name="json_list_entity", display_name="JSON List Entity"
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="items",
            field_type=FieldType.JSON,
            display_name="Items",
        )

        # Create record with JSON list
        json_list = [
            {"id": 1, "name": "Item 1"},
            {"id": 2, "name": "Item 2"},
            {"id": 3, "name": "Item 3"},
        ]

        record = await entities_service.create_record(
            entity_id=entity.id, data={"items": json_list}
        )

        assert record.field_data["items"] == json_list

    async def test_update_record_with_json_field(
        self, entities_service: CustomEntitiesService
    ):
        """Test updating a record's JSON field."""
        entity = await entities_service.create_entity(
            name="json_update_entity", display_name="JSON Update Entity"
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="metadata",
            field_type=FieldType.JSON,
            display_name="Metadata",
        )

        # Create initial record
        initial_data = {"version": 1, "tags": ["initial"]}
        record = await entities_service.create_record(
            entity_id=entity.id, data={"metadata": initial_data}
        )

        # Update the JSON field
        updated_data = {"version": 2, "tags": ["updated", "modified"]}
        updated_record = await entities_service.update_record(
            record_id=record.id, updates={"metadata": updated_data}
        )

        assert updated_record.field_data["metadata"] == updated_data

    async def test_json_field_validation_in_record_creation(
        self, entities_service: CustomEntitiesService
    ):
        """Test that JSON field validation is enforced during record creation."""
        entity = await entities_service.create_entity(
            name="json_validation_entity", display_name="JSON Validation Entity"
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="data",
            field_type=FieldType.JSON,
            display_name="Data",
        )

        # Try to create record with invalid JSON value (string instead of dict/list)
        with pytest.raises(TracecatValidationError, match="Expected dict or list"):
            await entities_service.create_record(
                entity_id=entity.id, data={"data": "not a dict or list"}
            )

        # Try to create record with too deeply nested structure
        deep_data = {"l1": {"l2": {"l3": {"l4": "too deep"}}}}
        with pytest.raises(TracecatValidationError, match="exceed"):
            await entities_service.create_record(
                entity_id=entity.id, data={"data": deep_data}
            )

        # Try to create record with nested arrays
        nested_arrays = [[1, 2], [3, 4]]
        # Accept both capitalized and lowercase phrasing from validator
        with pytest.raises(TracecatValidationError, match="(?i)nested arrays"):
            await entities_service.create_record(
                entity_id=entity.id, data={"data": nested_arrays}
            )

    async def test_json_field_with_complex_structure(
        self, entities_service: CustomEntitiesService
    ):
        """Test JSON field with a complex but valid structure."""
        entity = await entities_service.create_entity(
            name="complex_json_entity", display_name="Complex JSON Entity"
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="complex_data",
            field_type=FieldType.JSON,
            display_name="Complex Data",
        )

        # Complex structure with mixed types (within depth limit)
        complex_data = {
            "user": {
                "id": 123,
                "name": "John Doe",
                "preferences": {"notifications": True, "theme": "dark"},
            },
            "items": [{"id": 1, "value": 100}, {"id": 2, "value": 200}],
            "metadata": {"created_at": "2024-01-01", "tags": ["important", "reviewed"]},
        }

        record = await entities_service.create_record(
            entity_id=entity.id, data={"complex_data": complex_data}
        )

        assert record.field_data["complex_data"] == complex_data

    async def test_json_field_null_value(self, entities_service: CustomEntitiesService):
        """Test that JSON fields can store null values."""
        entity = await entities_service.create_entity(
            name="json_null_entity", display_name="JSON Null Entity"
        )

        await entities_service.create_field(
            entity_id=entity.id,
            field_key="optional_json",
            field_type=FieldType.JSON,
            display_name="Optional JSON",
        )

        # Create record with null JSON field
        record = await entities_service.create_record(
            entity_id=entity.id, data={"optional_json": None}
        )

        assert record.field_data.get("optional_json") is None

        # Create record without providing the JSON field
        record2 = await entities_service.create_record(entity_id=entity.id, data={})

        assert (
            "optional_json" not in record2.field_data
            or record2.field_data.get("optional_json") is None
        )
