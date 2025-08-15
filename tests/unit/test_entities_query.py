import uuid

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Entity, FieldMetadata, Record
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def query_builder(session: AsyncSession) -> EntityQueryBuilder:
    """Create a query builder instance for testing."""
    return EntityQueryBuilder(session)


@pytest.fixture
async def test_entity_with_fields(
    session: AsyncSession, svc_admin_role: Role
) -> tuple[Entity, list[FieldMetadata]]:
    """Create test entity with various field types."""
    service = CustomEntitiesService(session=session, role=svc_admin_role)

    # Create entity
    entity = await service.create_entity(
        name="query_test",
        display_name="Query Test Entity",
    )

    # Create fields of various types
    fields = []

    text_field = await service.create_field(
        entity_id=entity.id,
        field_key="name",
        field_type=FieldType.TEXT,
        display_name="Name",
    )
    fields.append(text_field)

    int_field = await service.create_field(
        entity_id=entity.id,
        field_key="age",
        field_type=FieldType.INTEGER,
        display_name="Age",
    )
    fields.append(int_field)

    number_field = await service.create_field(
        entity_id=entity.id,
        field_key="score",
        field_type=FieldType.NUMBER,
        display_name="Score",
    )
    fields.append(number_field)

    bool_field = await service.create_field(
        entity_id=entity.id,
        field_key="active",
        field_type=FieldType.BOOL,
        display_name="Active",
    )
    fields.append(bool_field)

    select_field = await service.create_field(
        entity_id=entity.id,
        field_key="status",
        field_type=FieldType.SELECT,
        display_name="Status",
        enum_options=["pending", "approved", "rejected"],
    )
    fields.append(select_field)

    array_field = await service.create_field(
        entity_id=entity.id,
        field_key="tags",
        field_type=FieldType.ARRAY_TEXT,
        display_name="Tags",
    )
    fields.append(array_field)

    multi_select_field = await service.create_field(
        entity_id=entity.id,
        field_key="categories",
        field_type=FieldType.MULTI_SELECT,
        display_name="Categories",
        enum_options=["cat1", "cat2", "cat3"],
    )
    fields.append(multi_select_field)

    date_field = await service.create_field(
        entity_id=entity.id,
        field_key="birth_date",
        field_type=FieldType.DATE,
        display_name="Birth Date",
    )
    fields.append(date_field)

    # Create test records
    await service.create_record(
        entity_id=entity.id,
        data={
            "name": "John Doe",
            "age": 30,
            "score": 85.5,
            "active": True,
            "status": "approved",
            "tags": ["tag1", "tag2"],
            "categories": ["cat1", "cat3"],
            "birth_date": "1993-01-15",
        },
    )

    await service.create_record(
        entity_id=entity.id,
        data={
            "name": "Jane Smith",
            "age": 25,
            "score": 92.0,
            "active": False,
            "status": "pending",
            "tags": ["tag2", "tag3"],
            "categories": ["cat2"],
            "birth_date": "1998-06-20",
        },
    )

    await service.create_record(
        entity_id=entity.id,
        data={
            "name": "Bob Johnson",
            "age": 35,
            "score": 78.5,
            "active": True,
            "status": "rejected",
            "tags": ["tag1"],
            "categories": ["cat1", "cat2"],
            "birth_date": "1988-11-30",
        },
    )

    return entity, fields


@pytest.mark.anyio
class TestEntityQueryBuilder:
    async def test_validate_field_not_found(
        self, query_builder: EntityQueryBuilder
    ) -> None:
        """Test validation raises error for non-existent field."""
        entity_id = uuid.uuid4()

        with pytest.raises(
            ValueError, match="Field 'nonexistent' not found or inactive"
        ):
            await query_builder._validate_field(entity_id, "nonexistent")

    async def test_validate_field_inactive(
        self,
        query_builder: EntityQueryBuilder,
        session: AsyncSession,
        svc_admin_role: Role,
    ) -> None:
        """Test validation raises error for inactive field."""
        service = CustomEntitiesService(session=session, role=svc_admin_role)

        # Create entity and field
        entity = await service.create_entity(
            name="inactive_test",
            display_name="Inactive Test",
        )

        field = await service.create_field(
            entity_id=entity.id,
            field_key="inactive_field",
            field_type=FieldType.TEXT,
            display_name="Inactive Field",
        )

        # Deactivate field
        await service.deactivate_field(field.id)

        # Try to validate inactive field
        with pytest.raises(
            ValueError, match="Field 'inactive_field' not found or inactive"
        ):
            await query_builder._validate_field(entity.id, "inactive_field")

    async def test_eq_operator(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test equality operator."""
        entity, fields = test_entity_with_fields

        # Test text equality
        expr = await query_builder.eq(entity.id, "name", "John Doe")
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1
        assert records[0].field_data["name"] == "John Doe"

        # Test integer equality
        expr = await query_builder.eq(entity.id, "age", 25)
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1
        assert records[0].field_data["age"] == 25

        # Test boolean equality
        expr = await query_builder.eq(entity.id, "active", True)
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 2  # John and Bob are active

    async def test_eq_type_validation(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
    ) -> None:
        """Test type validation for equality operator."""
        entity, fields = test_entity_with_fields

        # Wrong type for integer field
        with pytest.raises(TypeError, match="Expected int for age"):
            await query_builder.eq(entity.id, "age", "not an int")

        # Wrong type for boolean field
        with pytest.raises(TypeError, match="Expected bool for active"):
            await query_builder.eq(entity.id, "active", "not a bool")

        # Wrong type for text field
        with pytest.raises(TypeError, match="Expected string for name"):
            await query_builder.eq(entity.id, "name", 123)

    async def test_in_operator(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test IN operator."""
        entity, fields = test_entity_with_fields

        # Test IN with multiple values
        expr = await query_builder.in_(entity.id, "status", ["approved", "pending"])
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 2  # John (approved) and Jane (pending)

        statuses = {r.field_data["status"] for r in records}
        assert statuses == {"approved", "pending"}

    async def test_ilike_operator(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test case-insensitive pattern matching."""
        entity, fields = test_entity_with_fields

        # Test pattern with wildcard
        expr = await query_builder.ilike(entity.id, "name", "%doe%")
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1
        assert records[0].field_data["name"] == "John Doe"

        # Test case-insensitive matching
        expr = await query_builder.ilike(entity.id, "name", "%SMITH%")
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1
        assert records[0].field_data["name"] == "Jane Smith"

    async def test_ilike_non_text_field(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
    ) -> None:
        """Test that ilike rejects non-text fields."""
        entity, fields = test_entity_with_fields

        with pytest.raises(TypeError, match="Field age is not a text type"):
            await query_builder.ilike(entity.id, "age", "%25%")

    async def test_array_contains_operator(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test array containment operator."""
        entity, fields = test_entity_with_fields

        # Test array contains
        expr = await query_builder.array_contains(entity.id, "tags", ["tag1"])
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 2  # John and Bob have tag1

        # Test multi-select contains
        expr = await query_builder.array_contains(entity.id, "categories", ["cat2"])
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 2  # Jane and Bob have cat2

    async def test_array_contains_non_array_field(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
    ) -> None:
        """Test that array_contains rejects non-array fields."""
        entity, fields = test_entity_with_fields

        with pytest.raises(TypeError, match="Field name is not an array type"):
            await query_builder.array_contains(entity.id, "name", ["test"])

    async def test_between_operator(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test range operator."""
        entity, fields = test_entity_with_fields

        # Test integer range
        expr = await query_builder.between(entity.id, "age", 25, 30)
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 2  # Jane (25) and John (30)

        # Test number range
        expr = await query_builder.between(entity.id, "score", 80.0, 90.0)
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1  # John (85.5)

        # Test date range
        expr = await query_builder.between(
            entity.id, "birth_date", "1990-01-01", "1995-12-31"
        )
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1  # John (1993-01-15)

    async def test_between_non_numeric_field(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
    ) -> None:
        """Test that between rejects non-numeric/date fields."""
        entity, fields = test_entity_with_fields

        with pytest.raises(
            TypeError, match="Field name does not support range queries"
        ):
            await query_builder.between(entity.id, "name", "A", "Z")

    async def test_is_null_operator(
        self,
        query_builder: EntityQueryBuilder,
        session: AsyncSession,
        svc_admin_role: Role,
    ) -> None:
        """Test null checking operator."""
        service = CustomEntitiesService(session=session, role=svc_admin_role)

        # Create entity with fields
        entity = await service.create_entity(
            name="null_test",
            display_name="Null Test",
        )

        await service.create_field(
            entity_id=entity.id,
            field_key="optional_field",
            field_type=FieldType.TEXT,
            display_name="Optional Field",
        )

        # Create records with and without the field
        await service.create_record(
            entity_id=entity.id,
            data={"optional_field": "has value"},
        )

        await service.create_record(
            entity_id=entity.id,
            data={},  # Missing field
        )

        await service.create_record(
            entity_id=entity.id,
            data={"optional_field": None},  # Explicit null
        )

        # Test is_null
        expr = await query_builder.is_null(entity.id, "optional_field")
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 2  # record2 and record3

        # Test is_not_null
        expr = await query_builder.is_not_null(entity.id, "optional_field")
        stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
            expr,
        )
        result = await session.exec(stmt)
        records = result.all()
        assert len(records) == 1  # record1
        assert records[0].field_data["optional_field"] == "has value"

    async def test_build_query_complex(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test building complex queries with multiple filters."""
        entity, fields = test_entity_with_fields

        # Complex filter: active users between ages 20-35 with approved status
        filters = [
            {"field": "active", "operator": "eq", "value": True},
            {"field": "age", "operator": "between", "value": {"start": 20, "end": 35}},
            {"field": "status", "operator": "in", "value": ["approved", "pending"]},
        ]

        base_stmt = select(Record).where(
            Record.entity_id == entity.id,
            Record.owner_id == entity.owner_id,
        )

        stmt = await query_builder.build_query(base_stmt, entity.id, filters)
        result = await session.exec(stmt)
        records = result.all()

        # Should only match John (active, age 30, approved)
        assert len(records) == 1
        assert records[0].field_data["name"] == "John Doe"

    async def test_build_query_invalid_operator(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
    ) -> None:
        """Test that invalid operators are rejected."""
        entity, fields = test_entity_with_fields

        filters = [{"field": "age", "operator": "invalid_op", "value": 25}]

        base_stmt = select(Record)

        with pytest.raises(ValueError, match="Unsupported operator: invalid_op"):
            await query_builder.build_query(base_stmt, entity.id, filters)

    async def test_field_cache(
        self,
        query_builder: EntityQueryBuilder,
        test_entity_with_fields: tuple[Entity, list[FieldMetadata]],
        session: AsyncSession,
    ) -> None:
        """Test that field metadata is cached."""
        entity, fields = test_entity_with_fields

        # First call - field not in cache
        assert len(query_builder._field_cache) == 0
        field1 = await query_builder._validate_field(entity.id, "name")
        assert len(query_builder._field_cache) == 1

        # Second call - field should be retrieved from cache
        field2 = await query_builder._validate_field(entity.id, "name")
        assert len(query_builder._field_cache) == 1  # Still only one entry
        assert field1.id == field2.id  # Same field returned

        # Different field - adds to cache
        await query_builder._validate_field(entity.id, "age")
        assert len(query_builder._field_cache) == 2
