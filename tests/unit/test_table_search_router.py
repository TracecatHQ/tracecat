"""Error boundaries for table search routes; unexpected failures stay server errors."""

from typing import get_args
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.pagination import PaginationError, PaginationErrorCode
from tracecat.search.embeddings.service import WorkspaceEmbeddingService
from tracecat.tables.search.router import (
    get_table_search,
    get_table_search_progress,
    router,
    select_table_search_column,
)
from tracecat.tables.search.schemas import (
    TableSearchConfiguration,
    TableSearchProgressPage,
    TableSearchProgressParams,
    TableSearchSelection,
    TableSearchSelectionErrorResponse,
)
from tracecat.tables.search.service import TableSearchService

pytestmark = pytest.mark.anyio


@pytest.fixture
def search_role() -> Role:
    return Role(
        type="user",
        organization_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"table:read", "table:update", "workspace:read"}),
    )


@pytest.mark.parametrize("failure_at", ["configuration", "commit"])
async def test_unexpected_selection_response_failure_is_not_invalid_input(
    search_role: Role, monkeypatch: pytest.MonkeyPatch, failure_at: str
):
    session = AsyncMock(spec=AsyncSession)
    configuration = AsyncMock(return_value=TableSearchConfiguration())
    monkeypatch.setattr(TableSearchService, "select_column", AsyncMock())
    monkeypatch.setattr(TableSearchService, "configuration", configuration)
    failure = ValueError("Synthetic internal failure")
    if failure_at == "configuration":
        configuration.side_effect = failure
    else:
        session.commit.side_effect = failure

    with pytest.raises(ValueError) as exc:
        await select_table_search_column(
            table_id=uuid4(),
            params=TableSearchSelection(
                column_id=uuid4(), enabled=True, expected_generation=0
            ),
            role=search_role,
            session=session,
        )
    assert exc.value is failure
    if failure_at == "configuration":
        session.commit.assert_not_awaited()


async def test_invalid_column_selection_keeps_safe_422_response(
    search_role: Role, monkeypatch: pytest.MonkeyPatch
):
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(
        TableSearchService,
        "select_column",
        AsyncMock(side_effect=ValueError("Only TEXT columns support semantic search")),
    )
    with pytest.raises(HTTPException) as exc:
        await select_table_search_column(
            table_id=uuid4(),
            params=TableSearchSelection(
                column_id=uuid4(), enabled=True, expected_generation=0
            ),
            role=search_role,
            session=session,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {"code": "INVALID_SELECTION"}
    session.commit.assert_not_awaited()


async def test_missing_table_is_rejected_before_provider_lookup(
    search_role: Role, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        TableSearchService,
        "table",
        AsyncMock(side_effect=TracecatNotFoundError("Table not found")),
    )
    provider = AsyncMock()
    monkeypatch.setattr(WorkspaceEmbeddingService, "get", provider)
    with pytest.raises(HTTPException) as exc:
        await get_table_search(
            table_id=uuid4(), role=search_role, session=AsyncMock(spec=AsyncSession)
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == {"code": "NOT_FOUND"}
    provider.assert_not_awaited()


async def test_unexpected_provider_failure_is_not_a_table_404(
    search_role: Role, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(TableSearchService, "table", AsyncMock())
    failure = TracecatNotFoundError("Synthetic provider resource missing")
    monkeypatch.setattr(
        WorkspaceEmbeddingService, "get", AsyncMock(side_effect=failure)
    )
    with pytest.raises(TracecatNotFoundError) as exc:
        await get_table_search(
            table_id=uuid4(), role=search_role, session=AsyncMock(spec=AsyncSession)
        )
    assert exc.value is failure


@pytest.mark.parametrize(
    "scopes",
    [
        frozenset(),
        frozenset({"table:read"}),
        frozenset({"workspace:read"}),
        frozenset({"table:read", "workspace:read"}),
    ],
)
async def test_status_declares_both_scopes_before_reading_data(
    search_role: Role, monkeypatch: pytest.MonkeyPatch, scopes: frozenset[str]
):
    table = AsyncMock()
    provider = AsyncMock()
    configuration = AsyncMock(return_value=TableSearchConfiguration())
    monkeypatch.setattr(TableSearchService, "table", table)
    monkeypatch.setattr(TableSearchService, "configuration", configuration)
    monkeypatch.setattr(WorkspaceEmbeddingService, "get", provider)
    role = search_role.model_copy(update={"scopes": scopes})
    if scopes == frozenset({"table:read", "workspace:read"}):
        result = await get_table_search(
            table_id=uuid4(), role=role, session=AsyncMock(spec=AsyncSession)
        )
        assert result == TableSearchConfiguration()
        table.assert_awaited_once()
        provider.assert_awaited_once()
    else:
        with pytest.raises(ScopeDeniedError):
            await get_table_search(
                table_id=uuid4(), role=role, session=AsyncMock(spec=AsyncSession)
            )
        table.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.parametrize("malformed_request", [False, True])
async def test_selection_422_contract_covers_domain_and_request_errors(
    search_role: Role, monkeypatch: pytest.MonkeyPatch, malformed_request: bool
):
    app = FastAPI()
    app.include_router(router, prefix="/tables")

    async def authenticated_role():
        return search_role

    async def database_session():
        yield AsyncMock(spec=AsyncSession)

    app.dependency_overrides[get_args(WorkspaceActorRouteRole)[1].dependency] = (
        authenticated_role
    )
    app.dependency_overrides[get_async_session] = database_session
    select = AsyncMock(
        side_effect=ValueError("Only TEXT columns support semantic search")
    )
    monkeypatch.setattr(TableSearchService, "select_column", select)
    request = (
        {}
        if malformed_request
        else {"column_id": str(uuid4()), "enabled": True, "expected_generation": 0}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            f"/tables/{uuid4()}/search/selection", json=request
        )
    assert response.status_code == 422
    payload = response.json()
    TableSearchSelectionErrorResponse.model_validate(payload)
    if malformed_request:
        assert isinstance(payload["detail"], list)
        select.assert_not_awaited()
    else:
        assert payload["detail"] == {"code": "INVALID_SELECTION"}
        select.assert_awaited_once()

    # The same declared contract covers both real HTTP responses; documenting
    # only the custom error would hide FastAPI's ordinary request validation.
    schema = app.openapi()
    operation = schema["paths"]["/tables/{table_id}/search/selection"]["patch"]
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TableSearchSelectionErrorResponse"
    }
    detail = schema["components"]["schemas"]["TableSearchSelectionErrorResponse"][
        "properties"
    ]["detail"]
    assert detail["anyOf"] == [
        {"$ref": "#/components/schemas/TableSearchErrorRead"},
        {
            "type": "array",
            "items": {"$ref": "#/components/schemas/TableSearchRequestValidationError"},
        },
    ]


@pytest.mark.parametrize(
    "query,expected_status",
    [
        ({"generation": 1, "limit": 1, "cursor": "opaque"}, 200),
        ({"generation": 1, "limit": 101}, 422),
        ({"generation": 1, "limit": 0}, 422),
        ({"generation": 0}, 422),
        ({}, 422),
    ],
)
async def test_progress_uses_flat_shared_query_contract(
    search_role, monkeypatch, query, expected_status
):
    app = FastAPI()
    app.include_router(router, prefix="/tables")
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: search_role

    async def database_session():
        yield AsyncMock(spec=AsyncSession)

    app.dependency_overrides[get_async_session] = database_session
    progress = AsyncMock(return_value=TableSearchProgressPage(generation=1, items=[]))
    monkeypatch.setattr(TableSearchService, "progress", progress)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/tables/{uuid4()}/search/documents", params=query)
    assert response.status_code == expected_status
    if expected_status == 200:
        assert progress.call_args.kwargs["params"] == TableSearchProgressParams(**query)
        assert "prev_cursor" in response.json()
    else:
        progress.assert_not_awaited()
    operation = app.openapi()["paths"]["/tables/{table_id}/search/documents"]["get"]
    assert {p["name"] for p in operation["parameters"] if p["in"] == "query"} == {
        "generation",
        "limit",
        "cursor",
    }


async def test_invalid_progress_cursor_returns_safe_400(search_role, monkeypatch):
    monkeypatch.setattr(
        TableSearchService,
        "progress",
        AsyncMock(
            side_effect=PaginationError(
                "Synthetic private cursor", code=PaginationErrorCode.INVALID_CURSOR
            )
        ),
    )
    with pytest.raises(HTTPException) as error:
        await get_table_search_progress(
            table_id=uuid4(),
            role=search_role,
            session=AsyncMock(spec=AsyncSession),
            params=TableSearchProgressParams(generation=1, cursor="invalid"),
        )
    assert error.value.status_code == 400
    assert error.value.detail == {"code": "INVALID_CURSOR"}
    assert error.value.__context__ is None
