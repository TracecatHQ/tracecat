import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate
from tracecat.cases.records.models import (
    CaseRecordCreate,
    CaseRecordLink,
    CaseRecordUpdate,
)
from tracecat.cases.records.service import CaseRecordService
from tracecat.cases.service import CasesService
from tracecat.entities.enums import FieldType
from tracecat.entities.models import EntityCreate, EntityFieldCreate
from tracecat.entities.service import EntityService
from tracecat.records.model import RecordCreate
from tracecat.records.service import RecordService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)

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
        CaseRecordService(session=session, role=role_without_workspace)


# Fixtures


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:  # noqa: F811
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def entity_service(session: AsyncSession, svc_role: Role) -> EntityService:  # noqa: F811
    return EntityService(session=session, role=svc_role)


@pytest.fixture
async def records_service(session: AsyncSession, svc_role: Role) -> RecordService:  # noqa: F811
    return RecordService(session=session, role=svc_role)


@pytest.fixture
async def case_records_service(
    session: AsyncSession, svc_role: Role
) -> CaseRecordService:  # noqa: F811
    return CaseRecordService(session=session, role=svc_role)


@pytest.fixture
async def test_case(cases_service: CasesService):
    params = CaseCreate(
        summary="Records Case",
        description="Case for case-records service tests",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    case = await cases_service.create_case(params)
    return case


@pytest.fixture
async def entity_with_text_field(entity_service: EntityService):
    """Create an entity with a simple TEXT field to accept payloads."""
    entity = await entity_service.create_entity(
        EntityCreate(key="device", display_name="Device")
    )
    await entity_service.fields.create_field(
        entity,
        EntityFieldCreate(key="name", type=FieldType.TEXT, display_name="Name"),
    )
    return entity


# Tests


@pytest.mark.anyio
async def test_create_list_get_case_record(
    case_records_service: CaseRecordService,
    test_case,
    entity_with_text_field,
):
    # Create and link a new entity record
    created = await case_records_service.create_case_record(
        test_case,
        CaseRecordCreate(entity_key="device", data={"name": "Alice"}),
    )

    assert created.id is not None
    assert created.case_id == test_case.id
    assert created.entity_id == entity_with_text_field.id
    assert created.record is not None and created.record.data["name"] == "Alice"

    # List case records
    items = await case_records_service.list_case_records(test_case)
    assert len(items) == 1
    assert items[0].id == created.id

    # Get by link id
    fetched = await case_records_service.get_case_record(test_case, created.id)
    assert fetched is not None
    assert fetched.id == created.id

    # Get non-existent returns None
    assert (await case_records_service.get_case_record(test_case, uuid.uuid4())) is None


@pytest.mark.anyio
async def test_link_entity_record_and_duplicate_check(
    case_records_service: CaseRecordService,
    records_service: RecordService,
    test_case,
    entity_with_text_field,
):
    # Pre-create an entity record under the same entity
    rec = await records_service.create_record(
        entity_with_text_field, RecordCreate(data={"name": "Bob"})
    )

    # Link existing record to case
    link = await case_records_service.link_entity_record(
        test_case, CaseRecordLink(record_id=rec.id)
    )
    assert link.record_id == rec.id
    assert link.case_id == test_case.id

    # Linking the same record again should raise validation error
    with pytest.raises(TracecatValidationError):
        await case_records_service.link_entity_record(
            test_case, CaseRecordLink(record_id=rec.id)
        )

    # Linking a non-existent record should raise not found
    with pytest.raises(TracecatNotFoundError):
        await case_records_service.link_entity_record(
            test_case, CaseRecordLink(record_id=uuid.uuid4())
        )


@pytest.mark.anyio
async def test_update_case_record(
    case_records_service: CaseRecordService,
    test_case,
    entity_with_text_field,
):
    link = await case_records_service.create_case_record(
        test_case, CaseRecordCreate(entity_key="device", data={"name": "Init"})
    )

    updated = await case_records_service.update_case_record(
        link, CaseRecordUpdate(data={"name": "Updated"})
    )
    assert updated.id == link.id
    # Underlying record should reflect changes
    assert updated.record.data["name"] == "Updated"


@pytest.mark.anyio
async def test_unlink_case_record_preserves_entity_record(
    case_records_service: CaseRecordService,
    records_service: RecordService,
    test_case,
    entity_with_text_field,
):
    link = await case_records_service.create_case_record(
        test_case, CaseRecordCreate(entity_key="device", data={"name": "R"})
    )
    record_id = link.record_id

    # Unlink only removes the association
    await case_records_service.unlink_case_record(link)

    # Link is gone
    assert (await case_records_service.get_case_record(test_case, link.id)) is None

    # Entity record still exists
    rec = await records_service.get_record_by_id(record_id)
    assert rec.id == record_id


@pytest.mark.anyio
async def test_delete_case_record_removes_entity_record(
    case_records_service: CaseRecordService,
    records_service: RecordService,
    test_case,
    entity_with_text_field,
):
    link = await case_records_service.create_case_record(
        test_case, CaseRecordCreate(entity_key="device", data={"name": "Z"})
    )
    record_id = link.record_id

    await case_records_service.delete_case_record(link)

    # Underlying entity record should be deleted
    with pytest.raises(TracecatNotFoundError):
        await records_service.get_record_by_id(record_id)


@pytest.mark.anyio
async def test_max_records_limit_enforced(
    case_records_service: CaseRecordService,
    test_case,
    entity_with_text_field,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TRACECAT__MAX_RECORDS_PER_CASE", 1, raising=False)

    await case_records_service.create_case_record(
        test_case, CaseRecordCreate(entity_key="device", data={"name": "A"})
    )

    # Second attempt should exceed the limit
    with pytest.raises(TracecatValidationError):
        await case_records_service.create_case_record(
            test_case, CaseRecordCreate(entity_key="device", data={"name": "B"})
        )


@pytest.mark.anyio
async def test_create_case_record_invalid_entity_key(
    case_records_service: CaseRecordService, test_case
):
    with pytest.raises(TracecatNotFoundError):
        await case_records_service.create_case_record(
            test_case, CaseRecordCreate(entity_key="unknown", data={})
        )
