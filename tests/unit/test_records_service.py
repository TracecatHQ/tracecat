import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.entities.enums import FieldType
from tracecat.entities.schemas import (
    EntityCreate,
    EntityFieldCreate,
    EntityFieldOptionCreate,
)
from tracecat.entities.service import EntityService
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.records.model import RecordCreate, RecordUpdate
from tracecat.records.service import RecordService
from tracecat.types.pagination import CursorPaginationParams

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        service_id="tracecat-service",
        access_level=AccessLevel.BASIC,
    )
    with pytest.raises(TracecatAuthorizationError):
        RecordService(session=session, role=role_without_workspace)


@pytest.fixture
async def entity_service(session: AsyncSession, svc_role: Role) -> EntityService:
    return EntityService(session=session, role=svc_role)


@pytest.fixture
async def records_service(session: AsyncSession, svc_role: Role) -> RecordService:
    return RecordService(session=session, role=svc_role)


@pytest.fixture
async def entity_with_fields(entity_service: EntityService):
    # Create entity
    entity = await entity_service.create_entity(
        EntityCreate(key="device", display_name="Device")
    )

    # Add fields covering all supported types
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="age",
            type=FieldType.INTEGER,
            display_name="Age",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="ratio",
            type=FieldType.NUMBER,
            display_name="Ratio",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="name",
            type=FieldType.TEXT,
            display_name="Name",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="active",
            type=FieldType.BOOL,
            display_name="Active",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="meta",
            type=FieldType.JSON,
            display_name="Meta",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="seen_at",
            type=FieldType.DATETIME,
            display_name="Seen At",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="born_on",
            type=FieldType.DATE,
            display_name="Born On",
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="status",
            type=FieldType.SELECT,
            display_name="Status",
            options=[
                EntityFieldOptionCreate(label="new"),
                EntityFieldOptionCreate(label="active"),
            ],
        ),
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(
            key="tags",
            type=FieldType.MULTI_SELECT,
            display_name="Tags",
            options=[
                EntityFieldOptionCreate(label="blue"),
                EntityFieldOptionCreate(label="green"),
            ],
        ),
    )

    return entity


@pytest.mark.anyio
class TestRecordService:
    async def test_create_record_valid_payload(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        payload = RecordCreate(
            data={
                "age": "42",  # coerces to int
                "ratio": "3.14",  # coerces to float
                "name": 123,  # coerces to str
                "active": "true",  # coerces to bool
                "meta": {"k": "v"},  # dict ok
                "seen_at": "2023-01-01T12:30:00",
                "born_on": "2023-01-01",
                "status": "active",
                "tags": ["blue", "green"],
            }
        )
        rec = await records_service.create_record(entity, payload)
        assert rec.owner_id == records_service.workspace_id
        assert rec.entity_id == entity.id
        # Validate coerced types
        assert isinstance(rec.data["age"], int)
        assert isinstance(rec.data["ratio"], float)
        assert isinstance(rec.data["name"], str)
        assert isinstance(rec.data["active"], bool)
        assert isinstance(rec.data["meta"], dict)
        # Ensure ISO strings for datetime/date
        assert rec.data["seen_at"].startswith("2023-01-01T12:30:00")
        assert rec.data["born_on"] == "2023-01-01"
        assert rec.data["status"] == "active"
        assert set(rec.data["tags"]) == {"blue", "green"}

    async def test_create_record_rejects_unknown_key(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        with pytest.raises(ValueError):
            await records_service.create_record(
                entity_with_fields, RecordCreate(data={"unknown": 1})
            )

    async def test_create_record_rejects_invalid_select(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        with pytest.raises(ValueError):
            await records_service.create_record(
                entity_with_fields, RecordCreate(data={"status": "invalid"})
            )

    async def test_update_record_merges_and_validates(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        rec = await records_service.create_record(
            entity,
            RecordCreate(
                data={
                    "name": "initial",
                    "tags": ["blue"],
                }
            ),
        )

        updated = await records_service.update_record(
            rec,
            RecordUpdate(
                data={
                    "name": "changed",
                    "tags": ["green"],
                    "age": 5,
                }
            ),
        )
        assert updated.data["name"] == "changed"
        assert updated.data["tags"] == ["green"]
        assert updated.data["age"] == 5

        # Invalid update
        with pytest.raises(ValueError):
            await records_service.update_record(
                rec, RecordUpdate(data={"status": "bad"})
            )

    async def test_list_entity_records_paginated(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        # Create 3 records
        for i in range(3):
            await records_service.create_record(
                entity, RecordCreate(data={"name": f"n{i}", "age": i})
            )

        # Page 1
        page1 = await records_service.list_entity_records(
            entity, CursorPaginationParams(limit=2)
        )
        assert len(page1.items) == 2
        assert page1.has_more is True
        assert page1.next_cursor is not None

        # Page 2
        page2 = await records_service.list_entity_records(
            entity,
            CursorPaginationParams(limit=2, cursor=page1.next_cursor),
        )
        assert len(page2.items) >= 1
        assert page2.has_previous is True

    async def test_list_records_global_filtered(
        self, records_service: RecordService, entity_service: EntityService
    ) -> None:
        # Create two entities
        e1 = await entity_service.create_entity(EntityCreate(key="a", display_name="A"))
        e2 = await entity_service.create_entity(EntityCreate(key="b", display_name="B"))
        # Minimal field on both entities to accept payload
        for e in (e1, e2):
            await entity_service.fields.create_field(
                e, EntityFieldCreate(key="name", type=FieldType.TEXT, display_name="n")
            )
        # Create records under both
        await records_service.create_record(e1, RecordCreate(data={"name": "x"}))
        await records_service.create_record(e2, RecordCreate(data={"name": "y"}))

        # Filter by e1
        page = await records_service.list_records(
            CursorPaginationParams(limit=10), entity_id=e1.id
        )
        assert all(item.entity_id == e1.id for item in page.items)

    async def test_get_record_by_id(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        rec = await records_service.create_record(
            entity, RecordCreate(data={"name": "a"})
        )
        fetched = await records_service.get_record_by_id(rec.id)
        assert fetched.id == rec.id

    async def test_update_record_all_field_types(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        # Create baseline record with minimal valid data
        rec = await records_service.create_record(
            entity,
            RecordCreate(
                data={
                    "age": 1,
                    "ratio": 1.0,
                    "name": "n",
                    "active": True,
                    "meta": {"a": 1},
                    "seen_at": "2023-01-01T00:00:00",
                    "born_on": "2023-01-01",
                    "status": "active",
                    "tags": ["blue"],
                }
            ),
        )

        # Update each field to new values, exercising coercion
        updated = await records_service.update_record(
            rec,
            RecordUpdate(
                data={
                    "age": "100",  # str -> int
                    "ratio": "2.5",  # str -> float
                    "name": 987,  # int -> str
                    "active": "false",  # str -> bool
                    "meta": [1, 2, 3],  # list allowed for JSON
                    "seen_at": "2024-02-03T04:05:06",
                    "born_on": "2024-02-03",
                    "status": "new",
                    "tags": ["green"],
                }
            ),
        )

        assert updated.data["age"] == 100 and isinstance(updated.data["age"], int)
        assert updated.data["ratio"] == 2.5 and isinstance(updated.data["ratio"], float)
        assert updated.data["name"] == "987"
        assert updated.data["active"] is False
        assert updated.data["meta"] == [1, 2, 3]
        assert isinstance(updated.data["meta"], list)
        assert updated.data["seen_at"].startswith("2024-02-03T04:05:06")
        assert updated.data["born_on"] == "2024-02-03"
        assert updated.data["status"] == "new"
        assert updated.data["tags"] == ["green"]

    async def test_update_record_invalid_types(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        rec = await records_service.create_record(
            entity, RecordCreate(data={"name": "ok", "meta": {}})
        )
        # Invalid JSON (must be dict or list)
        with pytest.raises(ValueError):
            await records_service.update_record(rec, RecordUpdate(data={"meta": "str"}))
        # Invalid datetime format
        with pytest.raises(ValueError):
            await records_service.update_record(
                rec, RecordUpdate(data={"seen_at": "not-a-date"})
            )

    async def test_update_record_clear_values_with_none(
        self, records_service: RecordService, entity_with_fields
    ) -> None:
        entity = entity_with_fields
        rec = await records_service.create_record(
            entity,
            RecordCreate(
                data={
                    "age": 10,
                    "ratio": 1.1,
                    "name": "x",
                    "active": True,
                    "meta": {"k": "v"},
                    "seen_at": "2023-01-01T01:02:03",
                    "born_on": "2023-01-01",
                    "status": "active",
                    "tags": ["blue"],
                }
            ),
        )

        cleared = await records_service.update_record(
            rec,
            RecordUpdate(
                data={
                    "age": None,
                    "ratio": None,
                    "name": None,
                    "active": None,
                    "meta": None,
                    "seen_at": None,
                    "born_on": None,
                    "status": None,
                    "tags": None,
                }
            ),
        )
        # All explicitly set keys should now be None
        for k in [
            "age",
            "ratio",
            "name",
            "active",
            "meta",
            "seen_at",
            "born_on",
            "status",
            "tags",
        ]:
            assert cleared.data.get(k) is None

    async def test_record_surrogate_id_auto_generated(
        self, records_service: RecordService, entity_with_fields, session: AsyncSession
    ) -> None:
        """Test that surrogate_id is auto-generated and not null.

        This test catches migration issues where sequences might be missing.
        """
        entity = entity_with_fields
        rec = await records_service.create_record(
            entity, RecordCreate(data={"name": "test"})
        )

        # Refresh to ensure we have the latest from DB
        await session.refresh(rec)

        # Verify surrogate_id was auto-generated
        assert hasattr(rec, "surrogate_id"), "EntityRecord should have surrogate_id"
        assert rec.surrogate_id is not None, "surrogate_id should not be None"
        assert isinstance(rec.surrogate_id, int), "surrogate_id should be an integer"
        assert rec.surrogate_id > 0, "surrogate_id should be positive"

        # Create another record to verify incrementing
        rec2 = await records_service.create_record(
            entity, RecordCreate(data={"name": "test2"})
        )
        await session.refresh(rec2)

        assert rec2.surrogate_id is not None
        assert rec2.surrogate_id > rec.surrogate_id, (
            "surrogate_id should auto-increment"
        )
