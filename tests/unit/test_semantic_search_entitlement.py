"""Semantic search routes and background indexing require the entitlement."""

from typing import get_args
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    WorkspaceActorRouteRole,
    WorkspaceUserPathRole,
)
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.exceptions import EntitlementRequired
from tracecat.search.embeddings.router import router as embedding_router
from tracecat.search.indexing_types import (
    CollectionWork,
    IndexingOutcome,
    IndexingProgress,
)
from tracecat.search.indexing_workflow import index_search_collection
from tracecat.search.router import router as semantic_router
from tracecat.tables.search.router import router as table_search_router
from tracecat.tiers.enums import Entitlement

pytestmark = pytest.mark.anyio


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        organization_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"table:read", "table:update", "workspace:read"}),
    )


def _app(role: Role) -> FastAPI:
    app = FastAPI()
    app.include_router(table_search_router, prefix="/tables")
    app.include_router(semantic_router, prefix="/internal/tables")
    app.include_router(embedding_router)
    for annotated in (
        WorkspaceActorRouteRole,
        WorkspaceUserPathRole,
        ExecutorWorkspaceRole,
    ):
        app.dependency_overrides[get_args(annotated)[1].dependency] = lambda: role

    async def session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = session

    async def entitlement_required(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, EntitlementRequired)
        return JSONResponse(status_code=403, content={"entitlement": exc.entitlement})

    app.add_exception_handler(EntitlementRequired, entitlement_required)
    return app


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/tables/{table_id}/search", None),
        (
            "PATCH",
            "/tables/{table_id}/search/selection",
            {"column_id": str(uuid4()), "enabled": True, "expected_generation": 0},
        ),
        (
            "POST",
            "/tables/{table_id}/search/retry",
            {"expected_generation": 0, "document_ids": [str(uuid4())]},
        ),
        ("GET", "/tables/{table_id}/search/documents?generation=1", None),
        ("GET", "/workspaces/{workspace_id}/search/configuration", None),
        (
            "POST",
            "/internal/tables/synthetic/rows/semantic-search",
            {"query": "synthetic"},
        ),
    ],
)
async def test_semantic_search_routes_require_entitlement(
    role: Role, method: str, path: str, body: dict[str, object] | None
):
    url = path.format(table_id=uuid4(), workspace_id=role.workspace_id)
    with (
        patch(
            "tracecat.tables.search.router.TableSearchService",
            side_effect=AssertionError,
        ),
        patch(
            "tracecat.search.embeddings.router.WorkspaceEmbeddingService",
            side_effect=AssertionError,
        ),
        patch(
            "tracecat.search.router.TableRetrievalService", side_effect=AssertionError
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(role)), base_url="http://test"
        ) as http:
            response = await http.request(method, url, json=body)
    assert response.status_code == 403
    assert response.json() == {"entitlement": Entitlement.SEMANTIC_SEARCH.value}


async def test_entitled_org_reaches_semantic_search(role: Role):
    with (
        patch(
            "tracecat.tiers.access.is_org_entitled", new=AsyncMock(return_value=True)
        ),
        patch(
            "tracecat.search.embeddings.router.WorkspaceEmbeddingService.get",
            new=AsyncMock(side_effect=EntitlementRequired("sentinel")),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(role)), base_url="http://test"
        ) as http:
            response = await http.get(
                f"/workspaces/{role.workspace_id}/search/configuration"
            )
    # The route body ran, so the entitlement dependency let the request through.
    assert response.json() == {"entitlement": "sentinel"}


@pytest.mark.parametrize("entitled", [False, True])
async def test_indexing_skips_unentitled_organizations(entitled: bool):
    work = CollectionWork(
        organization_id=uuid4(), workspace_id=uuid4(), collection_id=uuid4()
    )
    capacity = patch(
        "tracecat.search.indexing_workflow.search_capacity",
        side_effect=RuntimeError("capacity reached"),
    )
    with (
        patch(
            "tracecat.search.indexing_workflow.get_async_session_bypass_rls_context_manager"
        ),
        patch(
            "tracecat.search.indexing_workflow.is_org_entitled",
            new=AsyncMock(return_value=entitled),
        ) as is_entitled,
        capacity as search_capacity,
    ):
        if entitled:
            with pytest.raises(Exception, match="SEARCH_UNAVAILABLE"):
                await index_search_collection(work)
            search_capacity.assert_called_once()
        else:
            result = await index_search_collection(work)
            assert result == IndexingProgress(outcome=IndexingOutcome.UNAVAILABLE)
            search_capacity.assert_not_called()
    is_entitled.assert_awaited_once()
    assert is_entitled.await_args is not None
    assert is_entitled.await_args.args[1:] == (
        work.organization_id,
        Entitlement.SEMANTIC_SEARCH,
    )
