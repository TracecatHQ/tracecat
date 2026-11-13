from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.cases.tag_definitions import internal_router as case_tags_router
from tracecat.db.engine import get_async_session
from tracecat.executor.action_gateway.app import create_app
from tracecat.executor.action_gateway.policy import (
    enforce_agent_script_gateway_access,
)
from tracecat.tags import internal_router as workflow_tags_router

GatewayFactory = Callable[[frozenset[str]], TestClient]


@pytest.fixture
def service() -> MagicMock:
    service = MagicMock()
    service.get_tag_by_ref_or_id = AsyncMock(
        return_value=SimpleNamespace(id=uuid.uuid4())
    )
    service.delete_tag = AsyncMock()
    return service


@pytest.fixture
def make_gateway(
    monkeypatch: pytest.MonkeyPatch, service: MagicMock
) -> Iterator[GatewayFactory]:
    clients: list[TestClient] = []

    def factory(scopes: frozenset[str]) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = (
            lambda: Role(
                type="service",
                service_id="tracecat-executor",
                organization_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                scopes=scopes,
            )
        )
        app.dependency_overrides[get_async_session] = lambda: AsyncMock()
        app.dependency_overrides[enforce_agent_script_gateway_access] = lambda: None
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    monkeypatch.setattr(case_tags_router, "CaseTagsService", lambda **_kwargs: service)
    monkeypatch.setattr(workflow_tags_router, "TagsService", lambda *_args: service)
    yield factory
    for client in clients:
        client.__exit__(None, None, None)


ROUTES = [
    pytest.param("/internal/case-tags/needs-triage", "case:delete", id="case-tag"),
    pytest.param("/internal/tags/needs-triage", "tag:delete", id="workflow-tag"),
]


@pytest.mark.parametrize(("path", "scope"), ROUTES)
def test_delete_tag_definition(
    make_gateway: GatewayFactory, service: MagicMock, path: str, scope: str
) -> None:
    response = make_gateway(frozenset({scope})).delete(path)

    assert response.status_code == 204
    service.get_tag_by_ref_or_id.assert_awaited_once_with("needs-triage")
    service.delete_tag.assert_awaited_once_with(
        service.get_tag_by_ref_or_id.return_value
    )


@pytest.mark.parametrize(("path", "scope"), ROUTES)
def test_delete_missing_tag_definition_returns_404(
    make_gateway: GatewayFactory, service: MagicMock, path: str, scope: str
) -> None:
    service.get_tag_by_ref_or_id.side_effect = NoResultFound()

    response = make_gateway(frozenset({scope})).delete(path)

    assert response.status_code == 404
    service.delete_tag.assert_not_awaited()


@pytest.mark.parametrize(("path", "scope"), ROUTES)
def test_delete_tag_definition_requires_scope(
    make_gateway: GatewayFactory, service: MagicMock, path: str, scope: str
) -> None:
    response = make_gateway(frozenset({"case:read", "tag:read"})).delete(path)

    assert response.status_code == 403
    service.delete_tag.assert_not_awaited()
