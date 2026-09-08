"""HTTP contracts for case aggregation."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import get_args
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.cases import internal_router
from tracecat.cases.enums import CasePriority
from tracecat.cases.schemas import CaseAggregateResponse
from tracecat.exceptions import TracecatValidationError
from tracecat.query.errors import TracecatQueryOverflowError, TracecatQueryTimeoutError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("limit", [None, config.TRACECAT__LIMIT_AGG_GROUPS_MAX])
async def test_aggregate_serialization(
    action_gateway_client: TestClient, test_admin_role: Role, limit: int | None
):
    assignee = uuid.uuid4()
    with patch.object(internal_router, "CasesService") as service:
        service.return_value.aggregate_cases = AsyncMock(
            return_value=CaseAggregateResponse(
                groups=[
                    {
                        "priority": CasePriority.HIGH,
                        "assignee": assignee,
                        "time": datetime.fromisoformat("2026-03-08T00:00:00-05:00"),
                        "day": date(2026, 3, 8),
                        "amount": Decimal("9007199254740992.1"),
                        "count": 2,
                        "sum": 3.5,
                        "missing": None,
                        "flag": True,
                    }
                ],
                truncated=False,
            )
        )
        response = action_gateway_client.post(
            "/internal/cases/aggregate",
            json={
                "group_by": ["priority"],
                **({"limit": limit} if limit is not None else {}),
            },
        )
    assert response.status_code == 200
    assert response.json() == {
        "groups": [
            {
                "priority": "high",
                "assignee": str(assignee),
                "time": "2026-03-08T05:00:00Z",
                "day": "2026-03-08",
                "amount": "9007199254740992.1",
                "count": 2,
                "sum": 3.5,
                "missing": None,
                "flag": True,
            }
        ],
        "truncated": False,
    }
    request = service.return_value.aggregate_cases.call_args.args[0]
    assert request.group_by[0].field == "priority"
    assert request.limit == (
        config.TRACECAT__LIMIT_AGG_GROUPS_DEFAULT if limit is None else limit
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"group_by": [], "limit": config.TRACECAT__LIMIT_AGG_GROUPS_MAX + 1},
        {"group_by": [], "limit": 0},
        {"group_by": [], "min_count": 2**63},
        {"group_by": ["status"] * 4},
        {"group_by": [], "aggs": []},
        {"group_by": ["status"], "aggs": [{"function": "count", "alias": "status"}]},
        {"group_by": [], "order_by": "missing"},
    ],
)
async def test_aggregate_invalid_request(
    action_gateway_client: TestClient, test_admin_role: Role, payload: dict[str, object]
):
    with patch.object(internal_router, "CasesService") as service:
        response = action_gateway_client.post("/internal/cases/aggregate", json=payload)
    assert response.status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TracecatValidationError("Unknown field"), 400, None),
        (TracecatQueryTimeoutError(), 422, "query_timeout"),
        (TracecatQueryOverflowError(), 400, "query_numeric_overflow"),
        (
            ProgrammingError(
                "SELECT private", {}, ValueError("private database detail")
            ),
            500,
            None,
        ),
    ],
)
async def test_aggregate_errors(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    error: Exception,
    status: int,
    code: str | None,
):
    with patch.object(internal_router, "CasesService") as service:
        service.return_value.aggregate_cases = AsyncMock(side_effect=error)
        response = action_gateway_client.post(
            "/internal/cases/aggregate", json={"group_by": []}
        )
    assert response.status_code == status
    if code:
        assert response.json()["detail"]["code"] == code
    assert "private database detail" not in response.text


async def test_aggregate_requires_read_scope(
    action_gateway_client: TestClient, test_admin_role: Role
):
    app = action_gateway_client.app
    assert isinstance(app, FastAPI)
    restricted_role = test_admin_role.model_copy(update={"scopes": frozenset()})
    with (
        patch.dict(
            app.dependency_overrides,
            {get_args(ExecutorWorkspaceRole)[1].dependency: lambda: restricted_role},
        ),
        patch.object(internal_router, "CasesService") as service,
    ):
        response = action_gateway_client.post(
            "/internal/cases/aggregate", json={"group_by": []}
        )
    assert response.status_code == 403
    service.assert_not_called()


async def test_aggregate_excluded_from_public_schema(action_gateway_client: TestClient):
    app = action_gateway_client.app
    assert isinstance(app, FastAPI)
    assert "/internal/cases/aggregate" not in app.openapi()["paths"]
