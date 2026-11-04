"""Invalid temporal case field values over the internal simple case routes."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from typing import get_args

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import CaseFieldCreate
from tracecat.cases.service import CaseFieldsService
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.db.models import Case, CaseEvent, Organization, Workspace
from tracecat.executor.action_gateway.app import create_app
from tracecat.tables.enums import SqlType

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]

FIELD_NAME = "observed_at"
SQL_TYPES = [SqlType.TIMESTAMPTZ, SqlType.DATE]
# A non-ISO string raises TypeError; an out-of-range epoch raises OverflowError.
INVALID_VALUES = ["next tuesday", 1e20]


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@pytest.fixture
async def role(
    session: AsyncSession, session_test_organization: Organization
) -> AsyncIterator[Role]:
    workspace = Workspace(
        name="temporal-fields-test", organization_id=session_test_organization.id
    )
    session.add(workspace)
    await session.flush()
    role = Role(
        type="service",
        service_id="tracecat-executor",
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        scopes=frozenset(
            {"case:create", "case:read", "case:update", "table:read", "table:create"}
        ),
    )
    token = ctx_role.set(role)
    try:
        yield role
    finally:
        ctx_role.reset(token)


@pytest.fixture
async def client(session: AsyncSession, role: Role) -> AsyncIterator[httpx.AsyncClient]:
    # Production routing, scope checks, and exception handlers; only the
    # identity and database session are test dependencies.
    app: FastAPI = create_app()

    async def override_role() -> Role:
        return role

    async def override_session() -> AsyncSession:
        return session

    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = (
        override_role
    )
    app.dependency_overrides[get_async_session] = override_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def _create_field(session: AsyncSession, role: Role, sql_type: SqlType) -> None:
    await CaseFieldsService(session=session, role=role).create_field(
        CaseFieldCreate(name=FIELD_NAME, type=sql_type)
    )


@pytest.mark.parametrize("value", INVALID_VALUES)
@pytest.mark.parametrize("sql_type", SQL_TYPES)
async def test_create_case_simple_rejects_invalid_temporal_field_value(
    session: AsyncSession,
    role: Role,
    client: httpx.AsyncClient,
    sql_type: SqlType,
    value: str | float,
) -> None:
    """An unparseable temporal field value is a client error, not a 500."""
    await _create_field(session, role, sql_type)

    response = await client.post(
        "/internal/cases/simple",
        json={
            "summary": "Synthetic case",
            "description": "Synthetic description",
            "priority": "medium",
            "severity": "low",
            "status": "new",
            "fields": {FIELD_NAME: value},
        },
    )

    assert response.status_code == 400, response.text
    assert FIELD_NAME in response.json()["detail"]


async def _add_case(session: AsyncSession, role: Role) -> Case:
    case = Case(
        workspace_id=role.workspace_id,
        summary="Synthetic case",
        description="Synthetic description",
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
    )
    session.add(case)
    await session.flush()
    return case


@pytest.mark.parametrize("value", INVALID_VALUES)
@pytest.mark.parametrize("sql_type", SQL_TYPES)
async def test_update_case_simple_rejects_invalid_temporal_field_value(
    session: AsyncSession,
    role: Role,
    client: httpx.AsyncClient,
    sql_type: SqlType,
    value: str | float,
) -> None:
    """An unparseable temporal field value is a client error, not a 500."""
    await _create_field(session, role, sql_type)
    case = await _add_case(session, role)

    response = await client.patch(
        f"/internal/cases/{case.id}/simple",
        json={"fields": {FIELD_NAME: value}},
    )

    assert response.status_code == 400, response.text
    assert FIELD_NAME in response.json()["detail"]


@pytest.mark.parametrize(
    ("sql_type", "value", "stored", "event_value"),
    [
        (
            SqlType.TIMESTAMPTZ,
            "2026-03-08T12:30:00+02:00",
            datetime(2026, 3, 8, 10, 30, tzinfo=UTC),
            "2026-03-08T10:30:00Z",
        ),
        (SqlType.DATE, "2026-03-08", date(2026, 3, 8), "2026-03-08"),
    ],
)
async def test_update_case_simple_stores_valid_temporal_field_value(
    session: AsyncSession,
    role: Role,
    client: httpx.AsyncClient,
    sql_type: SqlType,
    value: str,
    stored: datetime | date,
    event_value: str,
) -> None:
    """Valid ISO strings are stored, and the change event records them as ISO."""
    await _create_field(session, role, sql_type)
    case = await _add_case(session, role)

    response = await client.patch(
        f"/internal/cases/{case.id}/simple", json={"fields": {FIELD_NAME: value}}
    )

    assert response.status_code == 200, response.text
    fields = await CaseFieldsService(session=session, role=role).get_fields(case)
    assert fields is not None
    assert fields[FIELD_NAME] == stored
    event_data = await session.scalar(
        sa.select(CaseEvent.data).where(
            CaseEvent.case_id == case.id,
            CaseEvent.type == CaseEventType.FIELDS_CHANGED,
        )
    )
    assert event_data is not None
    assert event_data["changes"] == [
        {"field": FIELD_NAME, "old": None, "new": event_value}
    ]
