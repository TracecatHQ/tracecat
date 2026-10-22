"""Form creation error classification through public and executor routes."""

from collections.abc import AsyncIterator
from typing import get_args
from unittest.mock import AsyncMock

import pytest
from asyncpg import CheckViolationError, ForeignKeyViolationError, UniqueViolationError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import create_form, create_workflow
from tracecat.api.common import generic_exception_handler
from tracecat.auth.dependencies import ExecutorWorkspaceRole, WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.db.models import Workspace
from tracecat.forms import internal_router, router
from tracecat.forms.dependencies import (
    require_forms_enabled,
    require_forms_enabled_for_executor,
)
from tracecat.forms.exceptions import FormError, form_error_handler
from tracecat.forms.service import FormsService
from tracecat.identifiers.workflow import WorkflowUUID

pytestmark = pytest.mark.anyio


class _UnrelatedUniqueViolation(UniqueViolationError):
    constraint_name = "uq_unrelated_field"


@pytest.fixture(params=[False, True], ids=["public", "internal"])
async def form_client(
    request: pytest.FixtureRequest, session: AsyncSession, builder_role: Role
) -> AsyncIterator[tuple[AsyncClient, bool]]:
    internal: bool = request.param
    app = FastAPI()
    app.include_router(internal_router.router if internal else router.router)
    app.add_exception_handler(FormError, form_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    async def db_session() -> AsyncSession:
        return session

    async def role() -> Role:
        ctx_role.set(builder_role)
        return builder_role

    app.dependency_overrides[get_async_session] = db_session
    role_type = ExecutorWorkspaceRole if internal else WorkspaceActorRouteRole
    app.dependency_overrides[get_args(role_type)[1].dependency] = role
    gate = require_forms_enabled_for_executor if internal else require_forms_enabled
    app.dependency_overrides[gate] = lambda: None
    async with AsyncClient(
        transport=ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client, internal


async def test_form_create_race_returns_conflict(
    form_client: tuple[AsyncClient, bool],
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, internal = form_client
    workflow = await create_workflow(session, svc_workspace.id)
    await create_form(session, workflow)
    # Model a competing insert after the absence check, using the real constraint.
    monkeypatch.setattr(
        FormsService, "get_form_by_workflow_id", AsyncMock(return_value=None)
    )
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    workflow_id = WorkflowUUID.new(workflow.id).short()
    response = await client.request(
        "PUT" if internal else "POST",
        f"/internal/forms/workflows/{workflow_id}" if internal else "/forms",
        json={} if internal else {"workflow_id": workflow_id},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_exists"
    rollback.assert_awaited_once()


@pytest.mark.parametrize(
    "config",
    [
        {"title": "synthetic\x00title"},
        {"layout": [{"heading": "synthetic\x00heading"}]},
    ],
    ids=["title", "nested_heading"],
)
async def test_form_create_rejects_nul_before_database_write(
    form_client: tuple[AsyncClient, bool],
    session: AsyncSession,
    svc_workspace: Workspace,
    config: dict[str, object],
) -> None:
    client, internal = form_client
    workflow = await create_workflow(session, svc_workspace.id)
    workflow_id = WorkflowUUID.new(workflow.id).short()
    response = await client.request(
        "PUT" if internal else "POST",
        f"/internal/forms/workflows/{workflow_id}" if internal else "/forms",
        json={"config": config}
        if internal
        else {"workflow_id": workflow_id, "config": config},
    )
    assert response.status_code == 422
    assert await FormsService(session).get_form_by_workflow_id(workflow.id) is None


async def test_form_update_rejects_nul_before_database_write(
    form_client: tuple[AsyncClient, bool],
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, internal = form_client
    workflow = await create_workflow(session, svc_workspace.id)
    form = await create_form(session, workflow)
    commit = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", commit)
    workflow_id = WorkflowUUID.new(workflow.id).short()
    response = await client.request(
        "PUT" if internal else "PATCH",
        f"/internal/forms/workflows/{workflow_id}" if internal else f"/forms/{form.id}",
        json={"config": {"description": "synthetic\x00description"}},
    )
    assert response.status_code == 422
    commit.assert_not_awaited()


@pytest.mark.parametrize("form_client", [False], indirect=True, ids=["public"])
async def test_form_submit_rejects_nul_before_database_write(
    form_client: tuple[AsyncClient, bool],
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = form_client
    workflow = await create_workflow(session, svc_workspace.id)
    form = await create_form(session, workflow)
    commit = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", commit)
    response = await client.post(
        f"/forms/{form.id}/submissions",
        json={"values": {"synthetic": "invalid\x00value"}},
    )
    assert response.status_code == 422
    commit.assert_not_awaited()


@pytest.mark.parametrize(
    "driver_error",
    [
        ForeignKeyViolationError("synthetic database detail"),
        CheckViolationError("synthetic database detail"),
        _UnrelatedUniqueViolation("synthetic database detail"),
    ],
    ids=["foreign_key", "check", "unrelated_unique"],
)
async def test_form_create_unknown_integrity_error_stays_sanitized_500(
    form_client: tuple[AsyncClient, bool],
    session: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    driver_error: Exception,
) -> None:
    client, internal = form_client
    workflow = await create_workflow(session, svc_workspace.id)
    error = IntegrityError("synthetic statement", None, driver_error)
    monkeypatch.setattr(session, "commit", AsyncMock(side_effect=error))
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    workflow_id = WorkflowUUID.new(workflow.id).short()
    response = await client.request(
        "PUT" if internal else "POST",
        f"/internal/forms/workflows/{workflow_id}" if internal else "/forms",
        json={} if internal else {"workflow_id": workflow_id},
    )
    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }
    rollback.assert_awaited_once()
