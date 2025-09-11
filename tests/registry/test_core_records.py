"""Tests for core.records and case-record UDFs in the registry."""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry.core.records import (
    create_case_record,
    create_record,
    create_record_by_key,
    delete_case_record,
    delete_record,
    get_case_record,
    get_record,
    link_entity_record,
    list_case_records,
    list_entity_records,
    list_records,
    unlink_case_record,
    update_case_record,
)
from tracecat_registry.core.records import (
    update_record as update_entity_record,
)

from tracecat.records.model import RecordRead
from tracecat.types.exceptions import TracecatNotFoundError


@pytest.fixture(scope="session")
def redis_server():
    """Override Redis server fixture to no-op for this module."""
    yield


@pytest.fixture(autouse=True, scope="function")
def clean_redis_db(redis_server):
    """Override Redis cleanup to no-op for this module."""
    yield


@pytest.fixture
def mock_record():
    rec = MagicMock()
    rec.id = uuid.uuid4()
    rec.entity_id = uuid.uuid4()
    rec.data = {"field": "value"}
    rec.created_at = datetime.now()
    rec.updated_at = datetime.now()
    return rec


@pytest.fixture
def mock_record_read():
    return RecordRead(
        id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        data={"field": "value"},
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


@pytest.fixture
def mock_case():
    case = MagicMock()
    case.id = uuid.uuid4()
    return case


def _make_cursor_response(items):
    resp = MagicMock()
    resp.items = items
    resp.next_cursor = "next"
    resp.prev_cursor = "prev"
    resp.has_more = True
    resp.has_previous = False
    resp.total_estimate = 123
    return resp


@pytest.mark.anyio
class TestEntityRecordUDFs:
    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_list_records(self, mock_with_session, mock_record_read):
        mock_service = AsyncMock()
        mock_service.list_records.return_value = _make_cursor_response(
            [mock_record_read]
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await list_records()

        mock_service.list_records.assert_called_once()
        assert "items" in result and len(result["items"]) == 1
        assert result["next_cursor"] == "next"
        assert result["prev_cursor"] == "prev"
        assert result["has_more"] is True
        assert result["has_previous"] is False
        assert result["total_estimate"] == 123

    async def test_list_records_invalid_entity_id(self):
        with pytest.raises(ValueError):
            await list_records(entity_id="not-a-uuid")

    @patch("tracecat_registry.core.records.RecordService.with_session")
    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_list_entity_records(
        self, mock_entities_with_session, mock_records_with_session, mock_record_read
    ):
        # Entities
        mock_entities = AsyncMock()
        entity_obj = MagicMock()
        entity_obj.id = uuid.uuid4()
        mock_entities.get_entity.return_value = entity_obj
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        # Records
        mock_records = AsyncMock()
        mock_records.list_entity_records.return_value = _make_cursor_response(
            [mock_record_read]
        )
        mock_rec_ctx = AsyncMock()
        mock_rec_ctx.__aenter__.return_value = mock_records
        mock_records_with_session.return_value = mock_rec_ctx

        result = await list_entity_records(entity_id=str(entity_obj.id))

        mock_entities.get_entity.assert_called_once()
        mock_records.list_entity_records.assert_called_once()
        assert len(result["items"]) == 1

    async def test_list_entity_records_invalid_entity_id(self):
        with pytest.raises(ValueError):
            await list_entity_records(entity_id="bad-uuid")

    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_list_entity_records_entity_not_found(
        self, mock_entities_with_session
    ):
        mock_entities = AsyncMock()
        mock_entities.get_entity.side_effect = TracecatNotFoundError("Entity not found")
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        with pytest.raises(TracecatNotFoundError):
            await list_entity_records(entity_id=str(uuid.uuid4()))

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_get_record(self, mock_with_session, mock_record):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.return_value = mock_record
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await get_record(record_id=str(mock_record.id))
        assert result["id"] == str(mock_record.id)
        assert result["entity_id"] == str(mock_record.entity_id)
        assert result["data"] == mock_record.data

    async def test_get_record_invalid_id(self):
        with pytest.raises(ValueError):
            await get_record(record_id="nope")

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_get_record_not_found(self, mock_with_session):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.side_effect = TracecatNotFoundError(
            "Record not found"
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        with pytest.raises(TracecatNotFoundError):
            await get_record(record_id=str(uuid.uuid4()))

    @patch("tracecat_registry.core.records.RecordService.with_session")
    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_create_record(
        self, mock_entities_with_session, mock_records_with_session
    ):
        # Entities
        mock_entities = AsyncMock()
        entity_obj = MagicMock()
        entity_obj.id = uuid.uuid4()
        mock_entities.get_entity.return_value = entity_obj
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        # Records
        mock_records = AsyncMock()
        created = MagicMock()
        created.id = uuid.uuid4()
        created.entity_id = entity_obj.id
        created.data = {"a": 1}
        created.created_at = datetime.now()
        created.updated_at = datetime.now()
        mock_records.create_record.return_value = created
        mock_rec_ctx = AsyncMock()
        mock_rec_ctx.__aenter__.return_value = mock_records
        mock_records_with_session.return_value = mock_rec_ctx

        result = await create_record(entity_id=str(entity_obj.id), data={"a": 1})

        # Ensure correct types passed into service
        call = mock_records.create_record.call_args[0]
        assert call[0] is entity_obj  # entity
        assert call[1].data == {"a": 1}  # RecordCreate

        assert result["entity_id"] == str(entity_obj.id)
        assert result["data"] == {"a": 1}

    async def test_create_record_invalid_entity_id(self):
        with pytest.raises(ValueError):
            await create_record(entity_id="bad", data={})

    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_create_record_entity_not_found(self, mock_entities_with_session):
        mock_entities = AsyncMock()
        mock_entities.get_entity.side_effect = TracecatNotFoundError("Entity not found")
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        with pytest.raises(TracecatNotFoundError):
            await create_record(entity_id=str(uuid.uuid4()), data={})

    @patch("tracecat_registry.core.records.RecordService.with_session")
    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_create_record_bad_data(
        self, mock_entities_with_session, mock_records_with_session
    ):
        # Entities returns an entity
        mock_entities = AsyncMock()
        entity_obj = MagicMock()
        entity_obj.id = uuid.uuid4()
        mock_entities.get_entity.return_value = entity_obj
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        # Records.create_record raises ValueError
        mock_records = AsyncMock()
        mock_records.create_record.side_effect = ValueError("bad data")
        mock_rec_ctx = AsyncMock()
        mock_rec_ctx.__aenter__.return_value = mock_records
        mock_records_with_session.return_value = mock_rec_ctx

        with pytest.raises(TypeError):
            await create_record(entity_id=str(entity_obj.id), data={"bad": object()})

    @patch("tracecat_registry.core.records.RecordService.with_session")
    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_create_record_by_key(
        self, mock_entities_with_session, mock_records_with_session
    ):
        # Entities by key
        mock_entities = AsyncMock()
        entity_obj = MagicMock()
        entity_obj.id = uuid.uuid4()
        mock_entities.get_entity_by_key.return_value = entity_obj
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        # Records
        mock_records = AsyncMock()
        created = MagicMock()
        created.id = uuid.uuid4()
        created.entity_id = entity_obj.id
        created.data = {"b": 2}
        created.created_at = datetime.now()
        created.updated_at = datetime.now()
        mock_records.create_record.return_value = created
        mock_rec_ctx = AsyncMock()
        mock_rec_ctx.__aenter__.return_value = mock_records
        mock_records_with_session.return_value = mock_rec_ctx

        result = await create_record_by_key(entity_key="user", data={"b": 2})

        mock_entities.get_entity_by_key.assert_called_once_with("user")
        assert result["entity_id"] == str(entity_obj.id)
        assert result["data"] == {"b": 2}

    @patch("tracecat_registry.core.records.EntityService.with_session")
    async def test_create_record_by_key_entity_not_found(
        self, mock_entities_with_session
    ):
        mock_entities = AsyncMock()
        mock_entities.get_entity_by_key.side_effect = TracecatNotFoundError(
            "Entity not found"
        )
        mock_ent_ctx = AsyncMock()
        mock_ent_ctx.__aenter__.return_value = mock_entities
        mock_entities_with_session.return_value = mock_ent_ctx

        with pytest.raises(TracecatNotFoundError):
            await create_record_by_key(entity_key="missing", data={})

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_update_record(self, mock_with_session, mock_record):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.return_value = mock_record

        updated = MagicMock()
        updated.id = mock_record.id
        updated.entity_id = mock_record.entity_id
        updated.data = {"field": "new"}
        updated.created_at = mock_record.created_at
        updated.updated_at = datetime.now()
        mock_service.update_record.return_value = updated

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await update_entity_record(
            record_id=str(mock_record.id), data={"field": "new"}
        )

        mock_service.get_record_by_id.assert_called_once()
        mock_service.update_record.assert_called_once()
        assert result["data"] == {"field": "new"}

    async def test_update_record_invalid_id(self):
        with pytest.raises(ValueError):
            await update_entity_record(record_id="nope", data={})

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_update_record_not_found(self, mock_with_session):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.side_effect = TracecatNotFoundError(
            "Record not found"
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        with pytest.raises(TracecatNotFoundError):
            await update_entity_record(record_id=str(uuid.uuid4()), data={})

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_update_record_bad_data(self, mock_with_session, mock_record):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.return_value = mock_record
        mock_service.update_record.side_effect = ValueError("bad data")
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        with pytest.raises(TypeError):
            await update_entity_record(
                record_id=str(mock_record.id), data={"x": object()}
            )

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_delete_record(self, mock_with_session, mock_record):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.return_value = mock_record
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Should not raise
        await delete_record(record_id=str(mock_record.id))
        mock_service.delete_record.assert_called_once_with(mock_record)

    async def test_delete_record_invalid_id(self):
        with pytest.raises(ValueError):
            await delete_record(record_id="nope")

    @patch("tracecat_registry.core.records.RecordService.with_session")
    async def test_delete_record_not_found(self, mock_with_session):
        mock_service = AsyncMock()
        mock_service.get_record_by_id.side_effect = TracecatNotFoundError(
            "Record not found"
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        with pytest.raises(TracecatNotFoundError):
            await delete_record(record_id=str(uuid.uuid4()))


@pytest.fixture
def mock_case_record_link():
    link = MagicMock()
    link.id = uuid.uuid4()
    link.case_id = uuid.uuid4()
    link.entity_id = uuid.uuid4()
    link.record_id = uuid.uuid4()
    link.created_at = datetime.now()
    link.updated_at = datetime.now()

    # nested entity
    entity = MagicMock()
    entity.key = "user"
    entity.display_name = "User"
    link.entity = entity

    # nested record
    record = MagicMock()
    record.data = {"u": "v"}
    link.record = record
    return link


@pytest.mark.anyio
class TestCaseRecordUDFs:
    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_list_case_records(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        # CaseRecord service
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.list_case_records.return_value = [mock_case_record_link]
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        # Cases service instance
        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        result = await list_case_records(case_id=str(mock_case.id))

        mock_cases_instance.get_case.assert_called_once()
        mock_cr_service.list_case_records.assert_called_once_with(mock_case)
        assert result["total"] == 1
        assert result["items"][0]["entity_key"] == "user"

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_list_case_records_case_not_found(
        self, mock_cr_with_session, mock_cases_class, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = None
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(ValueError):
            await list_case_records(case_id=str(uuid.uuid4()))

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_get_case_record(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.get_case_record.return_value = mock_case_record_link
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        result = await get_case_record(
            case_id=str(mock_case.id), case_record_id=str(mock_case_record_link.id)
        )
        assert result["id"] == str(mock_case_record_link.id)
        assert result["record_id"] == str(mock_case_record_link.record_id)
        assert result["entity_key"] == "user"

    async def test_get_case_record_invalid_case_id(self):
        with pytest.raises(ValueError):
            await get_case_record(case_id="bad", case_record_id=str(uuid.uuid4()))

    async def test_get_case_record_invalid_link_id(self):
        with pytest.raises(ValueError):
            await get_case_record(case_id=str(uuid.uuid4()), case_record_id="bad")

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_get_case_record_case_not_found(
        self, mock_cr_with_session, mock_cases_class
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = None
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(ValueError):
            await get_case_record(
                case_id=str(uuid.uuid4()), case_record_id=str(uuid.uuid4())
            )

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_get_case_record_link_not_found(
        self, mock_cr_with_session, mock_cases_class, mock_case
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.get_case_record.return_value = None
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(ValueError):
            await get_case_record(
                case_id=str(mock_case.id), case_record_id=str(uuid.uuid4())
            )

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_create_case_record(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.create_case_record.return_value = mock_case_record_link
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        result = await create_case_record(
            case_id=str(mock_case.id), entity_key="user", data={"k": "v"}
        )

        # Validate service call param type
        call = mock_cr_service.create_case_record.call_args[0]
        assert call[0] is mock_case
        assert call[1].entity_key == "user"
        assert call[1].data == {"k": "v"}
        assert result["entity_key"] == "user"

    async def test_create_case_record_invalid_case_id(self):
        with pytest.raises(ValueError):
            await create_case_record(case_id="bad", entity_key="x", data={})

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_create_case_record_case_not_found(
        self, mock_cr_with_session, mock_cases_class
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = None
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(ValueError):
            await create_case_record(case_id=str(uuid.uuid4()), entity_key="x", data={})

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_create_case_record_entity_missing(
        self, mock_cr_with_session, mock_cases_class, mock_case
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.create_case_record.side_effect = TracecatNotFoundError(
            "Entity not found"
        )
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(TracecatNotFoundError):
            await create_case_record(
                case_id=str(mock_case.id), entity_key="missing", data={}
            )

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_link_entity_record(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.link_entity_record.return_value = mock_case_record_link
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        erid = uuid.uuid4()
        result = await link_entity_record(
            case_id=str(mock_case.id), entity_record_id=str(erid)
        )

        call = mock_cr_service.link_entity_record.call_args[0]
        assert call[0] is mock_case
        assert str(call[1].entity_record_id) == str(erid)
        assert result["record_id"] == str(mock_case_record_link.record_id)

    async def test_link_entity_record_invalid_ids(self):
        with pytest.raises(ValueError):
            await link_entity_record(case_id="bad", entity_record_id=str(uuid.uuid4()))
        with pytest.raises(ValueError):
            await link_entity_record(case_id=str(uuid.uuid4()), entity_record_id="bad")

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_link_entity_record_case_not_found(
        self, mock_cr_with_session, mock_cases_class
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = None
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(ValueError):
            await link_entity_record(
                case_id=str(uuid.uuid4()), entity_record_id=str(uuid.uuid4())
            )

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_link_entity_record_missing_entity_record(
        self, mock_cr_with_session, mock_cases_class, mock_case
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.link_entity_record.side_effect = TracecatNotFoundError(
            "Record not found"
        )
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        with pytest.raises(TracecatNotFoundError):
            await link_entity_record(
                case_id=str(mock_case.id), entity_record_id=str(uuid.uuid4())
            )

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_update_case_record(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.get_case_record.return_value = mock_case_record_link
        mock_cr_service.update_case_record.return_value = mock_case_record_link
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        result = await update_case_record(
            case_id=str(mock_case.id),
            case_record_id=str(mock_case_record_link.id),
            data={"f": "w"},
        )

        call = mock_cr_service.update_case_record.call_args[0]
        assert call[0] is mock_case_record_link
        assert call[1].data == {"f": "w"}
        assert result["id"] == str(mock_case_record_link.id)

    async def test_update_case_record_invalid_ids(self):
        with pytest.raises(ValueError):
            await update_case_record(
                case_id="bad", case_record_id=str(uuid.uuid4()), data={}
            )
        with pytest.raises(ValueError):
            await update_case_record(
                case_id=str(uuid.uuid4()), case_record_id="bad", data={}
            )

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_unlink_case_record(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.get_case_record.return_value = mock_case_record_link
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        result = await unlink_case_record(
            case_id=str(mock_case.id), case_record_id=str(mock_case_record_link.id)
        )
        mock_cr_service.unlink_case_record.assert_called_once_with(
            mock_case_record_link
        )
        assert result["action"] == "unlink"
        assert result["case_id"] == str(mock_case.id)
        assert result["record_id"] == str(mock_case_record_link.record_id)

    async def test_unlink_case_record_invalid_ids(self):
        with pytest.raises(ValueError):
            await unlink_case_record(case_id="bad", case_record_id=str(uuid.uuid4()))
        with pytest.raises(ValueError):
            await unlink_case_record(case_id=str(uuid.uuid4()), case_record_id="bad")

    @patch("tracecat_registry.core.records.CasesService")
    @patch("tracecat_registry.core.records.CaseRecordService.with_session")
    async def test_delete_case_record(
        self, mock_cr_with_session, mock_cases_class, mock_case, mock_case_record_link
    ):
        mock_cr_service = AsyncMock()
        mock_cr_service.session = MagicMock()
        mock_cr_service.role = MagicMock()
        mock_cr_service.get_case_record.return_value = mock_case_record_link
        mock_cr_ctx = AsyncMock()
        mock_cr_ctx.__aenter__.return_value = mock_cr_service
        mock_cr_with_session.return_value = mock_cr_ctx

        mock_cases_instance = AsyncMock()
        mock_cases_instance.get_case.return_value = mock_case
        mock_cases_class.return_value = mock_cases_instance

        result = await delete_case_record(
            case_id=str(mock_case.id), case_record_id=str(mock_case_record_link.id)
        )
        mock_cr_service.delete_case_record.assert_called_once_with(
            mock_case_record_link
        )
        assert result["action"] == "delete"
        assert result["case_id"] == str(mock_case.id)
        assert result["record_id"] == str(mock_case_record_link.record_id)

    async def test_delete_case_record_invalid_ids(self):
        with pytest.raises(ValueError):
            await delete_case_record(case_id="bad", case_record_id=str(uuid.uuid4()))
        with pytest.raises(ValueError):
            await delete_case_record(case_id=str(uuid.uuid4()), case_record_id="bad")
