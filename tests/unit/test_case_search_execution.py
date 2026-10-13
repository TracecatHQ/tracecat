"""Real PostgreSQL parity for the canonical case search transports."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, get_args
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from tests.unit.test_case_aggregation import (
    add_case,
    add_dropdown,
)
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.cases.enums import CasePriority, CaseSeverity
from tracecat.cases.router import cases_router
from tracecat.cases.schemas import CaseAggregateRequest
from tracecat.cases.search_schemas import CaseQuery, CaseSearchRequest
from tracecat.cases.service import CasesService, parse_case_short_id
from tracecat.db.models import (
    Case,
    CaseDropdownValue,
    CaseFields,
    CaseTag,
    CaseTagLink,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError
from tracecat.pagination import CursorPaginationParams
from tracecat.tiers import defaults as tier_defaults

pytestmark = pytest.mark.anyio
pytest_plugins = ["tests.unit.test_case_aggregation"]


@pytest.fixture
async def search_client(
    aggregate_app: FastAPI, aggregate_service: CasesService
) -> AsyncIterator[httpx.AsyncClient]:
    aggregate_app.include_router(cases_router)

    async def role():
        return aggregate_service.role

    aggregate_app.dependency_overrides[
        get_args(WorkspaceActorRouteRole)[1].dependency
    ] = role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aggregate_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def assert_query_parity(
    client: httpx.AsyncClient, filters: dict[str, Any], expected: int
) -> None:
    """Compare both search transports, status totals, and general aggregation."""
    # The open JSON dictionary is deliberate: this tests actual transport parsing.
    results = []
    for prefix in ("", "/internal"):
        response = await client.post(
            f"{prefix}/cases/search", json={"filters": filters}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == body["total_estimate"] == expected
        results.append({item["id"] for item in body["items"]})
        counts = await client.post(
            f"{prefix}/cases/search/aggregate", json={"filters": filters}
        )
        assert counts.status_code == 200, counts.text
        assert counts.json()["total"] == expected
        assert sum(counts.json()["status_groups"].values()) == expected
    aggregate = await client.post(
        "/internal/cases/aggregate", json={"group_by": [], "filters": filters}
    )
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json()["groups"] == [{"count": expected}]
    assert results[0] == results[1]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"field": "fields.amount", "op": "gte", "value": 2}, 1),
        ({"field": "fields.quantity", "op": "gt", "value": 2}, 2),
        ({"field": "fields.flag", "op": "eq", "value": True}, 1),
        ({"field": "fields.region", "op": "contains", "value": "alp"}, 1),
        ({"field": "fields.day", "op": "eq", "value": "2026-03-08"}, 3),
        ({"field": "fields.choice", "op": "eq", "value": "first"}, 3),
        ({"not": {"field": "fields.region", "op": "eq", "value": "alpha"}}, 1),
        ({"field": "fields.region", "op": "ne", "value": "alpha"}, 1),
        ({"field": "fields.region", "op": "not_in", "value": ["alpha"]}, 1),
        ({"field": "fields.region", "op": "is_null"}, 2),
        (
            {
                "or": [
                    {"field": "priority", "op": "eq", "value": "critical"},
                    {"field": "fields.quantity", "op": "gte", "value": 3},
                ]
            },
            2,
        ),
        (
            {
                "and": [
                    {"field": "priority", "op": "gte", "value": "high"},
                    {
                        "or": [
                            {"field": "fields.region", "op": "is_null"},
                            {
                                "not": {
                                    "field": "fields.flag",
                                    "op": "eq",
                                    "value": True,
                                }
                            },
                        ]
                    },
                ]
            },
            3,
        ),
    ],
)
async def test_scalar_boolean_tree_parity(
    search_client: httpx.AsyncClient,
    custom_cases: sa.Table,
    filters: dict[str, Any],
    expected: int,
) -> None:
    await assert_query_parity(search_client, filters, expected)


async def test_timestamp_physical_type_parity(
    search_client: httpx.AsyncClient,
    aggregate_service: CasesService,
    custom_cases: sa.Table,
) -> None:
    schema = aggregate_service.fields.schema_name
    await aggregate_service.session.execute(
        sa.text(
            f'ALTER TABLE "{schema}".case_fields ADD COLUMN observed_at TIMESTAMPTZ'
        )
    )
    await aggregate_service.session.execute(
        sa.text(f'UPDATE "{schema}".case_fields SET observed_at = :timestamp'),
        {"timestamp": datetime(2026, 3, 8, tzinfo=UTC)},
    )
    field_schema = await aggregate_service.session.scalar(
        sa.select(CaseFields).where(
            CaseFields.workspace_id == aggregate_service.workspace_id
        )
    )
    assert field_schema is not None
    field_schema.schema = {
        **field_schema.schema,
        "observed_at": {"type": "TIMESTAMPTZ"},
    }
    await aggregate_service.session.flush()
    await assert_query_parity(
        search_client,
        {"field": "fields.observed_at", "op": "gte", "value": "2026-03-08T00:00:00Z"},
        3,
    )


async def test_tag_and_dropdown_membership_null_parity(
    search_client: httpx.AsyncClient,
    aggregate_service: CasesService,
    custom_cases: sa.Table,
) -> None:
    service = aggregate_service
    cases = list(
        (
            await service.session.scalars(
                sa.select(Case)
                .where(Case.workspace_id == service.workspace_id)
                .order_by(Case.surrogate_id)
            )
        ).all()
    )
    red = CaseTag(workspace_id=service.workspace_id, name="Synthetic red", ref="red")
    blue = CaseTag(workspace_id=service.workspace_id, name="Synthetic blue", ref="blue")
    service.session.add_all([red, blue])
    await service.session.flush()
    service.session.add_all(
        [
            CaseTagLink(case_id=cases[0].id, tag_id=red.id),
            CaseTagLink(case_id=cases[0].id, tag_id=blue.id),
            CaseTagLink(case_id=cases[1].id, tag_id=red.id),
        ]
    )
    definition, selected = await add_dropdown(service, "verdict")
    service.session.add(
        CaseDropdownValue(
            case_id=cases[0].id, definition_id=definition.id, option_id=selected.id
        )
    )
    await service.session.flush()
    for filters, expected in [
        ({"field": "tags", "op": "in", "value": ["red", "blue"]}, 2),
        (
            {
                "and": [
                    {"field": "tags", "op": "contains", "value": "red"},
                    {"field": "tags", "op": "contains", "value": "blue"},
                ]
            },
            1,
        ),
        ({"not": {"field": "tags", "op": "in", "value": ["red"]}}, 2),
        ({"field": "tags", "op": "is_null"}, 2),
        ({"field": "dropdowns.verdict", "op": "is_null"}, 3),
        ({"field": "dropdowns.verdict", "op": "ne", "value": selected.ref}, 0),
        ({"not": {"field": "dropdowns.verdict", "op": "eq", "value": selected.ref}}, 0),
        (
            {
                "or": [
                    {
                        "field": "dropdowns.verdict",
                        "op": "not_in",
                        "value": [selected.ref],
                    },
                    {"field": "dropdowns.verdict", "op": "is_null"},
                ]
            },
            3,
        ),
    ]:
        await assert_query_parity(search_client, filters, expected)
    legacy = await search_client.get(
        "/cases/search", params={"tags": "red", "dropdown": f"verdict:{selected.ref}"}
    )
    canonical = await search_client.post(
        "/internal/cases/search",
        json={"tags": ["red"], "dropdown": [f"verdict:{selected.ref}"]},
    )
    assert legacy.status_code == canonical.status_code == 200
    assert (
        [item["id"] for item in legacy.json()["items"]]
        == [item["id"] for item in canonical.json()["items"]]
        == [str(cases[0].id)]
    )


@pytest.mark.parametrize("prefix", ["", "/internal"])
async def test_workspace_identity_and_max_depth_are_conjunctive(
    search_client: httpx.AsyncClient, aggregate_service: CasesService, prefix: str
) -> None:
    service = aggregate_service
    local = await add_case(service)
    other = Workspace(name="synthetic-foreign", organization_id=service.organization_id)
    service.session.add(other)
    await service.session.flush()
    foreign = Case(
        workspace_id=other.id,
        summary="Synthetic foreign",
        description="Synthetic",
        priority=CasePriority.CRITICAL,
        severity=CaseSeverity.HIGH,
    )
    service.session.add(foreign)
    await service.session.flush()
    body = {
        "filters": {
            "or": [
                {"and": [{"not": {"field": "status", "op": "eq", "value": "closed"}}]}
            ]
        },
        "status": ["new"],
        "case_id": str(foreign.id),
    }
    response = await search_client.post(f"{prefix}/cases/search", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    body["case_id"] = str(local.id)
    response = await search_client.post(f"{prefix}/cases/search", json=body)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [str(local.id)]
    assert parse_case_short_id(" CASE-0042 ") == 42


@pytest.mark.parametrize(
    "filters",
    [
        {"not": {"field": "tags", "op": "contains", "value": "missing"}},
        {"not": {"field": "dropdowns.missing", "op": "is_null"}},
        {"field": "fields.missing", "op": "eq", "value": 1},
        {"field": "priority", "op": "contains", "value": "critical"},
    ],
)
async def test_unknown_metadata_and_operators_fail_on_every_entry(
    search_client: httpx.AsyncClient, filters: dict[str, Any]
) -> None:
    for path, body in [
        ("/cases/search", {"filters": filters}),
        ("/internal/cases/search", {"filters": filters}),
        ("/cases/search/aggregate", {"filters": filters}),
        ("/internal/cases/search/aggregate", {"filters": filters}),
        ("/internal/cases/aggregate", {"filters": filters, "group_by": []}),
    ]:
        response = await search_client.post(path, json=body)
        assert response.status_code == 400, response.text


async def test_scope_denial_on_legacy_canonical_and_service_paths(
    search_client: httpx.AsyncClient, aggregate_service: CasesService
) -> None:
    service = aggregate_service
    service.role = service.role.model_copy(update={"scopes": frozenset()})
    for prefix in ("", "/internal"):
        for path in ("/cases", "/cases/search"):
            response = await search_client.get(f"{prefix}{path}")
            assert response.status_code == 403, response.text
        for path in ("/cases/search", "/cases/search/aggregate"):
            response = await search_client.post(f"{prefix}{path}", json={})
            assert response.status_code == 403, response.text
    with patch.object(
        service,
        "_search_service",
        side_effect=AssertionError("unauthorized metadata access"),
    ):
        for invoke in (
            lambda: service.query_cases(CaseSearchRequest()),
            lambda: service.query_case_aggregates(CaseQuery()),
            lambda: service.aggregate_cases(CaseAggregateRequest(group_by=[])),
            lambda: service.search_cases(CursorPaginationParams(), tag_ids=[uuid4()]),
            lambda: service.get_search_case_aggregates(tag_ids=[uuid4()]),
            lambda: service.list_cases(limit=1),
        ):
            with pytest.raises(ScopeDeniedError):
                await invoke()


async def test_negated_dropdown_entitlement_denial_is_typed(
    search_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"case_addons": False}),
    )
    filters = {"not": {"field": "dropdowns.synthetic", "op": "is_null"}}
    for prefix in ("", "/internal"):
        for path in ("/cases/search", "/cases/search/aggregate"):
            response = await search_client.post(
                f"{prefix}{path}", json={"filters": filters}
            )
            assert response.status_code == 403, response.text
            assert response.json()["type"] == "EntitlementRequired"
        response = await search_client.get(
            f"{prefix}/cases/search", params={"dropdown": "synthetic:selected"}
        )
        assert response.status_code == 403, response.text


async def test_builtin_queries_skip_field_schema_and_strict_body_rejects_scope(
    search_client: httpx.AsyncClient, aggregate_service: CasesService
) -> None:
    with patch.object(
        aggregate_service.fields,
        "get_field_schema",
        side_effect=AssertionError("unexpected schema read"),
    ):
        assert (await aggregate_service.query_cases(CaseSearchRequest())).items == []
        assert (await aggregate_service.query_case_aggregates(CaseQuery())).total == 0
        assert (
            await aggregate_service.aggregate_cases(CaseAggregateRequest(group_by=[]))
        ).groups == [{"count": 0}]
    for prefix in ("", "/internal"):
        response = await search_client.post(
            f"{prefix}/cases/search", json={"workspace_id": str(uuid4())}
        )
        assert response.status_code == 422, response.text
        response = await search_client.post(
            f"{prefix}/cases/search", json={"field_ids": ["id"]}
        )
        assert response.status_code == 400, response.text
