"""The case-view routes enforce case:read at the HTTP boundary."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseViewVisibility
from tracecat.cases.search_schemas import CaseQuery
from tracecat.cases.views import router as views_router
from tracecat.cases.views.schemas import CaseViewRead
from tracecat.db.engine import get_async_session
from tracecat.exceptions import ScopeDeniedError
from tracecat.pagination import CursorPaginatedResponse

VIEW_ID = "00000000-0000-4000-8000-000000000001"
READ = CaseViewRead(
    id=VIEW_ID,
    kind="custom",
    name="Synthetic view",
    visibility=CaseViewVisibility.PERSONAL,
    query=CaseQuery(),
    can_edit=True,
)

# (method, path, JSON body) for every public case-view endpoint.
ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/case-views", None),
    ("GET", "/case-views/builtins", None),
    ("POST", "/case-views", {"name": "Synthetic view"}),
    ("GET", f"/case-views/{VIEW_ID}", None),
    ("PATCH", f"/case-views/{VIEW_ID}", {"name": "Renamed"}),
    ("DELETE", f"/case-views/{VIEW_ID}", None),
    ("PUT", f"/case-views/{VIEW_ID}/pin", {}),
    ("DELETE", f"/case-views/{VIEW_ID}/pin", None),
]


def _client(
    monkeypatch: pytest.MonkeyPatch, scopes: frozenset[str], *, raise_errors: bool
) -> Iterator[TestClient]:
    service = MagicMock()
    service.list_views = AsyncMock(return_value=CursorPaginatedResponse(items=[]))
    service.list_builtins = MagicMock(return_value=[])
    for name in ("create_view", "get_view", "update_view", "pin_view"):
        setattr(service, name, AsyncMock(return_value=READ))
    service.delete_view = AsyncMock(return_value=None)
    service.unpin_view = AsyncMock(return_value=None)

    def service_factory(_session: AsyncSession, _role: Role) -> MagicMock:
        return service

    app = FastAPI()
    app.include_router(views_router.router)

    def role() -> Role:
        return Role(
            type="user",
            service_id="tracecat-api",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scopes=scopes,
        )

    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = role
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    monkeypatch.setattr(views_router, "CaseViewsService", service_factory)
    with TestClient(app, raise_server_exceptions=raise_errors) as client:
        yield client


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, frozenset({"case:read"}), raise_errors=False)


@pytest.fixture
def outsider(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Raise server errors so the test sees the scope denial itself. The API
    # app's exception handler turns ScopeDeniedError into a 403.
    yield from _client(monkeypatch, frozenset({"workflow:read"}), raise_errors=True)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_case_read_is_enough_for_every_view_route(
    reader: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    response = reader.request(method, path, json=body)

    assert response.status_code < 300, response.text


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_view_routes_reject_callers_without_case_read(
    outsider: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    with pytest.raises(ScopeDeniedError):
        outsider.request(method, path, json=body)


def test_endpoint_table_covers_every_route() -> None:
    routes = {
        (method, route.path)
        for route in views_router.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
    }
    covered = {
        (method, path.replace(VIEW_ID, "{view_id}")) for method, path, _ in ENDPOINTS
    }
    assert routes == covered
