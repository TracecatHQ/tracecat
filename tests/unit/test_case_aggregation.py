"""Case aggregation against real PostgreSQL, including HTTP serialization."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.service import CasesService
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.db.models import Case, CaseFields, Organization, User, Workspace
from tracecat.executor.action_gateway.app import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
async def aggregate_service(
    session: AsyncSession, session_test_organization: Organization
) -> AsyncIterator[CasesService]:
    workspace = Workspace(
        name="aggregate-test", organization_id=session_test_organization.id
    )
    session.add(workspace)
    await session.flush()
    role = Role(
        type="service",
        service_id="tracecat-executor",
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        scopes=frozenset({"case:read", "table:read", "table:create"}),
    )
    token = ctx_role.set(role)
    try:
        yield CasesService(session, role=role)
    finally:
        ctx_role.reset(token)


@pytest.fixture
async def aggregate_app(aggregate_service: CasesService) -> FastAPI:
    # Use production routing, middleware, scope checks, and exception handlers.
    # Only the authenticated identity and database session are test dependencies.
    app = create_app()

    async def role() -> Role:
        return aggregate_service.role

    async def session() -> AsyncSession:
        return aggregate_service.session

    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = role
    app.dependency_overrides[get_async_session] = session
    return app


@pytest.fixture
async def aggregate_client(aggregate_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aggregate_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def add_case(
    service: CasesService,
    *,
    priority: CasePriority = CasePriority.HIGH,
    severity: CaseSeverity = CaseSeverity.HIGH,
    status: CaseStatus = CaseStatus.NEW,
    created_at: datetime | None = None,
) -> Case:
    case = Case(
        workspace_id=service.workspace_id,
        summary="Synthetic case",
        description="Synthetic description",
        priority=priority,
        severity=severity,
        status=status,
        created_at=created_at or datetime(2026, 3, 8, 12, tzinfo=UTC),
    )
    service.session.add(case)
    await service.session.flush()
    return case


@pytest.fixture
async def custom_cases(aggregate_service: CasesService) -> sa.Table:
    service = aggregate_service
    schema = service.fields.schema_name
    await service.session.execute(sa.schema.CreateSchema(schema))
    table = sa.Table(
        "case_fields",
        sa.MetaData(),
        sa.Column("case_id", sa.UUID, primary_key=True),
        sa.Column("region", sa.Text),
        sa.Column("amount", sa.Numeric),
        sa.Column("quantity", sa.BigInteger),
        sa.Column("flag", sa.Boolean),
        sa.Column("choice", sa.Text),
        sa.Column("day", sa.Date),
        sa.Column("link", JSONB),
        schema=schema,
    )
    connection = await service.session.connection()
    await connection.run_sync(table.create)
    service.session.add(
        CaseFields(
            workspace_id=service.workspace_id,
            schema={
                "region": {"type": "TEXT"},
                "amount": {"type": "NUMERIC"},
                "quantity": {"type": "INTEGER"},
                "flag": {"type": "BOOLEAN"},
                "choice": {"type": "SELECT"},
                "day": {"type": "DATE"},
                "link": {"type": "JSONB", "kind": "URL"},
            },
        )
    )
    for i, region in enumerate(["alpha", "beta", None, None]):
        case = await add_case(service)
        if i < 3:
            await service.session.execute(
                table.insert().values(
                    case_id=case.id,
                    region=region,
                    amount=[Decimal("1.5"), Decimal("2.5"), None][i],
                    quantity=[1, 3, 5][i],
                    flag=i == 0,
                    choice="first",
                    day=datetime(2026, 3, 8).date(),
                    link={"url": "https://example.com/item", "label": "Example"}
                    if i == 0
                    else None,
                )
            )
    await service.session.flush()
    return table


async def test_custom_nulls_and_negation(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    custom_cases: sa.Table,
):
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": ["fields.region"],
            "order_by": "fields.region",
            "sort": "asc",
        },
    )
    assert response.status_code == 200
    assert response.json()["groups"] == [
        {"fields.region": "alpha", "count": 1},
        {"fields.region": "beta", "count": 1},
        {"fields.region": None, "count": 2},
    ]
    for predicate, expected in [
        ({"field": "fields.region", "op": "is_null"}, 2),
        ({"not": {"field": "fields.region", "op": "eq", "value": "alpha"}}, 1),
        ({"field": "fields.region", "op": "ne", "value": "alpha"}, 1),
        (
            {
                "or": [
                    {"field": "fields.region", "op": "eq", "value": "alpha"},
                    {"field": "fields.region", "op": "is_null"},
                ]
            },
            3,
        ),
        (
            {
                "and": [
                    {"not": {"field": "fields.region", "op": "eq", "value": "alpha"}},
                    {"field": "priority", "op": "gte", "value": "high"},
                ]
            },
            1,
        ),
        ({"field": "fields.region", "op": "in", "value": []}, 0),
        ({"field": "fields.region", "op": "not_in", "value": []}, 4),
    ]:
        response = await aggregate_client.post(
            "/internal/cases/aggregate", json={"group_by": [], "filters": predicate}
        )
        assert response.status_code == 200, response.text
        assert response.json()["groups"] == [{"count": expected}]


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("amount", [2, 2, 4.0, 2.0, 2.0, 1.5, 2.5]),
        ("quantity", [3, 3, 9.0, 3.0, 3.0, 1, 5]),
    ],
)
async def test_numeric_aggregates_without_custom_group(
    aggregate_client: httpx.AsyncClient,
    custom_cases: sa.Table,
    field: str,
    expected: list[int | float],
):
    functions = ["count", "count_distinct", "sum", "mean", "median", "min", "max"]
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": ["priority"],
            "aggs": [
                {"function": f, "field": f"fields.{field}", "alias": f}
                for f in functions
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [
        {"priority": "high", **dict(zip(functions, expected, strict=True))}
    ]
    assert isinstance(response.json()["groups"][0]["sum"], float)


async def test_custom_report_preserves_wire_types_and_reuses_join(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    custom_cases: sa.Table,
):
    user = User(email="assignee@example.com", hashed_password="unused", is_active=True)
    aggregate_service.session.add(user)
    await aggregate_service.session.flush()
    await aggregate_service.session.execute(
        sa.update(Case)
        .where(
            Case.id.in_(
                sa.select(custom_cases.c.case_id).where(
                    custom_cases.c.region == "alpha"
                )
            )
        )
        .values(assignee_id=user.id)
    )
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": ["assignee_id", "fields.region", "fields.flag"],
            "aggs": [{"function": "sum", "field": "fields.quantity"}],
            "filters": {"field": "fields.region", "op": "eq", "value": "alpha"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [
        {
            "assignee_id": str(user.id),
            "fields.region": "alpha",
            "fields.flag": True,
            "sum_quantity": 1.0,
        }
    ]
    await aggregate_service.session.execute(
        sa.text("SET LOCAL TIME ZONE 'Pacific/Honolulu'")
    )
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": ["fields.choice", {"field": "fields.day", "bucket": "month"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [
        {"fields.choice": "first", "fields.day": "2026-03-01", "count": 3},
        {"fields.choice": None, "fields.day": None, "count": 1},
    ]
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": [{"field": "fields.day", "bucket": "day", "timezone": "UTC"}],
        },
    )
    assert response.status_code == 400


async def test_precision_text_and_url(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    custom_cases: sa.Table,
):
    await aggregate_service.session.execute(
        custom_cases.update()
        .where(custom_cases.c.region == "alpha")
        .values(region="x" * 256 + "a", amount=Decimal("9007199254740992.1"))
    )
    await aggregate_service.session.execute(
        custom_cases.update()
        .where(custom_cases.c.region == "beta")
        .values(region="x" * 256 + "b", amount=Decimal("9007199254740992.2"))
    )
    for field, expected in [
        (
            "region",
            [
                {"fields.region": "x" * 256, "count": 2},
                {"fields.region": None, "count": 2},
            ],
        ),
        (
            "amount",
            [
                {"fields.amount": "9007199254740992.1", "count": 1},
                {"fields.amount": "9007199254740992.2", "count": 1},
                {"fields.amount": None, "count": 2},
            ],
        ),
        (
            "link",
            [
                {"fields.link": "https://example.com/item", "count": 1},
                {"fields.link": None, "count": 3},
            ],
        ),
    ]:
        response = await aggregate_client.post(
            "/internal/cases/aggregate",
            json={
                "group_by": [f"fields.{field}"],
                "order_by": f"fields.{field}",
                "sort": "asc",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["groups"] == expected


@pytest.mark.parametrize("field", ["priority", "severity", "status"])
async def test_enum_order(
    aggregate_service: CasesService, aggregate_client: httpx.AsyncClient, field: str
):
    enum = {"priority": CasePriority, "severity": CaseSeverity, "status": CaseStatus}[
        field
    ]
    for member in reversed(list(enum)):
        case = await add_case(aggregate_service)
        setattr(case, field, member)
    await aggregate_service.session.flush()
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={"group_by": [field], "order_by": field, "sort": "asc"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [
        {field: member.value, "count": 1} for member in enum
    ]


async def test_severity_range_and_workspace_isolation(
    aggregate_service: CasesService, aggregate_client: httpx.AsyncClient
):
    for severity in CaseSeverity:
        await add_case(aggregate_service, severity=severity)
    other = Workspace(
        name="other-aggregate-test",
        organization_id=aggregate_service.role.organization_id,
    )
    aggregate_service.session.add(other)
    await aggregate_service.session.flush()
    other_role = aggregate_service.role.model_copy(update={"workspace_id": other.id})
    await add_case(
        CasesService(aggregate_service.session, other_role), severity=CaseSeverity.FATAL
    )
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": ["severity"],
            "filters": {"field": "severity", "op": "gte", "value": "high"},
            "order_by": "severity",
            "sort": "asc",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [
        {"severity": s, "count": 1} for s in ["high", "critical", "fatal"]
    ]


@pytest.mark.parametrize(
    ("bucket", "instants", "expected"),
    [
        (
            "day",
            ["2026-03-08T06:00:00Z", "2026-03-09T05:00:00Z"],
            ["2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"],
        ),
        (
            "hour",
            ["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"],
            ["2026-11-01T05:00:00Z", "2026-11-01T06:00:00Z"],
        ),
        (
            "week",
            ["2026-03-08T12:00:00Z", "2026-03-09T12:00:00Z"],
            ["2026-03-02T05:00:00Z", "2026-03-09T04:00:00Z"],
        ),
        (
            "month",
            ["2026-03-31T12:00:00Z", "2026-04-01T12:00:00Z"],
            ["2026-03-01T05:00:00Z", "2026-04-01T04:00:00Z"],
        ),
    ],
)
async def test_time_buckets(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    bucket: str,
    instants: list[str],
    expected: list[str],
):
    for instant in reversed(instants):
        await add_case(aggregate_service, created_at=datetime.fromisoformat(instant))
    await aggregate_service.session.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={
            "group_by": [
                {
                    "field": "created_at",
                    "bucket": bucket,
                    "timezone": "America/New_York",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [
        {"created_at": instant, "count": 1} for instant in expected
    ]


@pytest.mark.parametrize("resource", ["cases", "tables"])
async def test_reporting_limits_validation_and_transaction_reuse(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    resource: str,
):
    path, field = "/internal/cases/aggregate", "priority"
    if resource == "tables":
        response = await aggregate_client.post(
            "/internal/tables",
            json={
                "name": "report",
                "columns": [{"name": "priority", "type": "TEXT"}],
            },
        )
        assert response.status_code == 201, response.text
        path = "/internal/tables/report/aggregate"
    response = await aggregate_client.post(path, json={"group_by": []})
    assert response.status_code == 200, response.text
    assert response.json() == {"groups": [{"count": 0}], "truncated": False}
    for priority in [CasePriority.LOW, CasePriority.HIGH, CasePriority.HIGH]:
        if resource == "cases":
            await add_case(aggregate_service, priority=priority)
        else:
            response = await aggregate_client.post(
                "/internal/tables/report/rows", json={"data": {field: priority.value}}
            )
            assert response.status_code == 201, response.text
    await aggregate_service.session.execute(
        sa.text("SET LOCAL statement_timeout = '5min'")
    )
    all_groups = [{field: "high", "count": 2}, {field: "low", "count": 1}]
    for extra, expected, truncated in [
        ({"limit": 1}, all_groups[:1], True),
        ({"limit": 2}, all_groups, False),
        ({"limit": config.TRACECAT__LIMIT_AGG_GROUPS_MAX}, all_groups, False),
        ({"min_count": 2}, all_groups[:1], False),
        ({"min_count": 2**31}, [], False),
        ({"min_count": 2**63 - 1}, [], False),
    ]:
        response = await aggregate_client.post(
            path, json={"group_by": [field], **extra}
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"groups": expected, "truncated": truncated}
    for extra in [
        {"limit": 0},
        {"limit": config.TRACECAT__LIMIT_AGG_GROUPS_MAX + 1},
        {"min_count": 2**63},
        {"aggs": []},
        {"order_by": "missing"},
    ]:
        response = await aggregate_client.post(path, json={"group_by": [], **extra})
        assert response.status_code == 422, response.text
    response = await aggregate_client.post(path, json={"group_by": []})
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [{"count": 3}]
    assert (
        await aggregate_service.session.scalar(sa.text("SHOW statement_timeout"))
        == "5min"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"group_by": ["fields.absent"]},
        {"group_by": ["tags"]},
        {"group_by": ["dropdowns.absent"]},
        {"group_by": ["created_at"]},
        {"group_by": ["short_id"]},
        {"group_by": [], "filters": {"field": "status", "op": "gte", "value": "new"}},
        {
            "group_by": [],
            "filters": {"field": "severity", "op": "gte", "value": "unknown"},
        },
        {
            "group_by": [],
            "filters": {"field": "status", "op": "eq", "value": "INVALID"},
        },
    ],
)
async def test_semantic_errors_without_custom_schema(
    aggregate_client: httpx.AsyncClient, payload: dict[str, object]
):
    response = await aggregate_client.post("/internal/cases/aggregate", json=payload)
    assert response.status_code == 400, response.text


async def test_custom_overflow_preserves_error_contract(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    custom_cases: sa.Table,
):
    await aggregate_service.session.execute(
        custom_cases.update().values(amount=Decimal("1e400"))
    )
    response = await aggregate_client.post(
        "/internal/cases/aggregate",
        json={"group_by": [], "aggs": [{"function": "sum", "field": "fields.amount"}]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "query_numeric_overflow"
    # Rolling back the failed query's savepoint leaves the caller's session usable.
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": []}
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [{"count": 4}]
    # Missing physical storage produces a real programming error, not a mocked exception.
    connection = await aggregate_service.session.connection()
    await connection.run_sync(custom_cases.drop)
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": ["fields.region"]}
    )
    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }


async def test_real_timeout_through_endpoint(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    await add_case(aggregate_service)
    schema = aggregate_service.fields.schema_name
    await aggregate_service.session.execute(sa.schema.CreateSchema(schema))
    # A real slow view makes cancellation deterministic without mocking execution.
    # The schema name is generated exclusively from the fixture workspace UUID.
    connection = await aggregate_service.session.connection()
    quoted_schema = connection.dialect.identifier_preparer.quote_schema(schema)
    await aggregate_service.session.execute(
        sa.text(
            f"CREATE VIEW {quoted_schema}.case_fields AS "
            "SELECT id AS case_id, 1 AS slow FROM public.case CROSS JOIN pg_sleep(0.05)"
        )
    )
    aggregate_service.session.add(
        CaseFields(
            workspace_id=aggregate_service.workspace_id,
            schema={"slow": {"type": "INTEGER"}},
        )
    )
    await aggregate_service.session.flush()
    monkeypatch.setattr(config, "TRACECAT__AGG_STATEMENT_TIMEOUT_MS", 1)
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": ["fields.slow"]}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "query_timeout"
    monkeypatch.setattr(config, "TRACECAT__AGG_STATEMENT_TIMEOUT_MS", 30_000)
    assert (
        await aggregate_service.session.scalar(sa.text("SHOW statement_timeout"))
        == "5min"
    )
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": []}
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [{"count": 1}]


async def test_custom_fields_do_not_cross_workspaces(
    aggregate_service: CasesService,
    aggregate_client: httpx.AsyncClient,
    custom_cases: sa.Table,
):
    other = Workspace(
        name="other-custom-test", organization_id=aggregate_service.role.organization_id
    )
    aggregate_service.session.add(other)
    await aggregate_service.session.flush()
    other_role = aggregate_service.role.model_copy(update={"workspace_id": other.id})
    foreign_case = await add_case(CasesService(aggregate_service.session, other_role))
    # Even a backing row referencing a foreign case must not enter the result.
    await aggregate_service.session.execute(
        custom_cases.insert().values(case_id=foreign_case.id, region="foreign")
    )
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": ["fields.region"]}
    )
    assert response.status_code == 200, response.text
    assert sum(group["count"] for group in response.json()["groups"]) == 4
    assert all(
        group["fields.region"] != "foreign" for group in response.json()["groups"]
    )


@pytest.mark.parametrize("field", ["raw_json", "multi"])
async def test_unsupported_custom_types(
    aggregate_service: CasesService, aggregate_client: httpx.AsyncClient, field: str
):
    aggregate_service.session.add(
        CaseFields(
            workspace_id=aggregate_service.workspace_id,
            schema={"raw_json": {"type": "JSONB"}, "multi": {"type": "MULTI_SELECT"}},
        )
    )
    await aggregate_service.session.flush()
    for payload in [
        {"group_by": [f"fields.{field}"]},
        {"group_by": [], "aggs": [{"function": "count", "field": f"fields.{field}"}]},
        {"group_by": [], "filters": {"field": f"fields.{field}", "op": "is_null"}},
    ]:
        response = await aggregate_client.post(
            "/internal/cases/aggregate", json=payload
        )
        assert response.status_code == 400, response.text


async def test_gateway_scope_enforcement_and_schema_visibility(
    aggregate_app: FastAPI,
    aggregate_client: httpx.AsyncClient,
    aggregate_service: CasesService,
):
    await add_case(aggregate_service)
    restricted = aggregate_service.role.model_copy(update={"scopes": frozenset()})
    dependency = get_args(ExecutorWorkspaceRole)[1].dependency
    aggregate_app.dependency_overrides[dependency] = lambda: restricted
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": []}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "insufficient_scope"
    aggregate_app.dependency_overrides[dependency] = lambda: aggregate_service.role
    response = await aggregate_client.post(
        "/internal/cases/aggregate", json={"group_by": []}
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"] == [{"count": 1}]
    assert "/internal/cases/aggregate" not in aggregate_app.openapi()["paths"]
