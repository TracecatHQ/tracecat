"""Tests for the internal forms routes that back the core.forms actions."""

import uuid
from typing import Any, cast, get_args
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import create_form, create_workflow, publish_workflow
from tests._route_utils import EffectiveRoute, iter_effective_api_routes
from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session
from tracecat.db.models import Form, FormSubmission, Workflow, Workspace
from tracecat.executor.action_gateway.app import create_app
from tracecat.executor.action_gateway.policy import (
    enforce_agent_script_gateway_access,
)
from tracecat.feature_flags import FeatureFlag
from tracecat.forms import internal_router
from tracecat.forms.dependencies import require_forms_enabled_for_executor
from tracecat.forms.enums import FormStatus
from tracecat.forms.schemas import FormConfig, FormUpdate
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

EXPECTS: dict[str, dict[str, Any]] = {
    "model": {"type": 'enum["mba", "mbp"]'},
    "justification": {"type": "str"},
}

EXPECTED_ROUTES = {
    ("GET", "/internal/forms"),
    ("GET", "/internal/forms/{form_id}"),
    ("PUT", "/internal/forms/workflows/{workflow_id}"),
    ("GET", "/internal/forms/{form_id}/submissions"),
    ("GET", "/internal/forms/submissions/{submission_id}"),
    ("DELETE", "/internal/forms/submissions/{submission_id}"),
}


def _gateway_forms_routes() -> list[EffectiveRoute]:
    return [
        route
        for route in iter_effective_api_routes(create_app())
        if route.path.startswith("/internal/forms")
    ]


@pytest.fixture
def executor_role(svc_workspace: Workspace) -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
async def workflow(
    session: AsyncSession, svc_workspace: Workspace, executor_role: Role
) -> Workflow:
    workflow = await create_workflow(session, svc_workspace.id)
    await publish_workflow(session, executor_role, workflow, EXPECTS)
    return workflow


async def _submission(session: AsyncSession, form: Form) -> FormSubmission:
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        answers={"model": "mba", "justification": "Mine broke"},
        definition_version=1,
        wf_exec_id=f"{WorkflowUUID.new(form.workflow_id).short()}/exec_test",
    )
    session.add(submission)
    await session.commit()
    return submission


async def _stored(session: AsyncSession, submission_id: uuid.UUID) -> bool:
    statement = select(FormSubmission.id).where(FormSubmission.id == submission_id)
    return (await session.scalar(statement)) is not None


# ----- Mounting and gating -----


def test_action_gateway_serves_exactly_the_forms_routes() -> None:
    routes = {
        (method, route.path)
        for route in _gateway_forms_routes()
        for method in route.methods
    }

    assert routes == EXPECTED_ROUTES


def test_every_internal_forms_route_has_the_gate() -> None:
    for route in _gateway_forms_routes():
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert require_forms_enabled_for_executor in calls, route.path


def test_executor_holds_the_scopes_the_routes_need() -> None:
    scopes = SERVICE_PRINCIPAL_SCOPES["tracecat-executor"]

    assert {
        "form:read",
        "form:create",
        "form:update",
        "form:submission:read",
        "form:submission:delete",
    } <= scopes
    # Workflows read and edit forms. They do not submit or delete them.
    assert "form:submit" not in scopes
    assert "form:delete" not in scopes


async def _gateway_get(role: Role, path: str) -> httpx.Response:
    app = create_app()
    role_dependency = get_args(ExecutorWorkspaceRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: role
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    app.dependency_overrides[enforce_agent_script_gateway_access] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as http:
        return await http.get(path)


async def test_flag_off_returns_404(
    executor_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())

    response = await _gateway_get(executor_role, "/internal/forms")

    assert response.status_code == 404


async def test_entitlement_off_returns_403(
    executor_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.FORMS})

    with patch(
        "tracecat.tiers.access.is_org_entitled", new=AsyncMock(return_value=False)
    ):
        response = await _gateway_get(executor_role, "/internal/forms")

    assert response.status_code == 403
    assert response.json()["type"] == "EntitlementRequired"


# ----- Behaviour -----


async def test_upsert_creates_the_form_then_updates_it(
    session: AsyncSession, executor_role: Role, workflow: Workflow
) -> None:
    workflow_id = WorkflowUUID.new(workflow.id)

    created = await internal_router.upsert_form(
        role=executor_role,
        session=session,
        workflow_id=workflow_id,
        params=FormUpdate(config=FormConfig(title="Laptop request")),
    )

    assert created.status is FormStatus.OFFLINE
    assert created.config.title == "Laptop request"
    assert created.workflow_id == workflow_id

    updated = await internal_router.upsert_form(
        role=executor_role,
        session=session,
        workflow_id=workflow_id,
        params=FormUpdate(status=FormStatus.ONLINE),
    )

    assert updated.id == created.id
    assert updated.status is FormStatus.ONLINE
    # A status-only update keeps the config.
    assert updated.config.title == "Laptop request"
    forms = (await session.scalars(select(Form))).all()
    assert len(forms) == 1


async def test_upsert_rejects_a_config_that_does_not_resolve(
    session: AsyncSession, executor_role: Role, workflow: Workflow
) -> None:
    params = FormUpdate(
        config=FormConfig.model_validate({"layout": [{"field": "not_an_input"}]})
    )

    with pytest.raises(HTTPException) as exc_info:
        await internal_router.upsert_form(
            role=executor_role,
            session=session,
            workflow_id=WorkflowUUID.new(workflow.id),
            params=params,
        )

    assert exc_info.value.status_code == 422
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail["code"] == "form_config_invalid"
    assert detail["issues"][0]["code"] == "unknown_field"
    assert (await session.scalars(select(Form))).first() is None


async def test_upsert_for_an_unknown_workflow_is_404(
    session: AsyncSession, executor_role: Role
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await internal_router.upsert_form(
            role=executor_role,
            session=session,
            workflow_id=WorkflowUUID.new(uuid.uuid4()),
            params=FormUpdate(),
        )

    assert exc_info.value.status_code == 404


async def test_list_and_get_include_offline_forms(
    session: AsyncSession, executor_role: Role, workflow: Workflow
) -> None:
    form = await create_form(
        session, workflow, status=FormStatus.OFFLINE, title="Laptop request"
    )

    page = await internal_router.list_forms(
        role=executor_role, session=session, page=PageParams()
    )
    read = await internal_router.get_form(
        role=executor_role, session=session, form_id=form.id
    )

    assert [item.id for item in page.items] == [form.id]
    assert read.config.title == "Laptop request"


async def test_a_run_reads_its_submission_with_only_the_submission_id(
    session: AsyncSession, executor_role: Role, workflow: Workflow
) -> None:
    form = await create_form(session, workflow)
    submission = await _submission(session, form)

    with patch(
        "tracecat.forms.submissions.WorkflowExecutionsService.connect",
        new=AsyncMock(side_effect=RuntimeError("no temporal in this test")),
    ):
        read = await internal_router.get_submission(
            role=executor_role, session=session, submission_id=submission.id
        )
        page = await internal_router.list_submissions(
            role=executor_role,
            session=session,
            form_id=form.id,
            page=PageParams(),
            submitted_after=None,
            submitted_before=None,
        )

    assert read.id == submission.id
    assert read.form_id == form.id
    assert read.answers == {"model": "mba", "justification": "Mine broke"}
    assert read.wf_exec_id == submission.wf_exec_id
    assert [item.id for item in page.items] == [submission.id]


async def test_delete_submission_removes_it(
    session: AsyncSession, executor_role: Role, workflow: Workflow
) -> None:
    form = await create_form(session, workflow)
    submission = await _submission(session, form)
    submission_id = submission.id

    await internal_router.delete_submission(
        role=executor_role, session=session, submission_id=submission_id
    )

    assert not await _stored(session, submission_id)
    with pytest.raises(HTTPException) as exc_info:
        await internal_router.get_submission(
            role=executor_role, session=session, submission_id=submission_id
        )
    assert exc_info.value.status_code == 404


async def test_submissions_of_another_workspace_are_not_found(
    session: AsyncSession, executor_role: Role, workflow: Workflow
) -> None:
    form = await create_form(session, workflow)
    submission_id = (await _submission(session, form)).id
    other_workspace = Workspace(
        name="other-workspace", organization_id=executor_role.organization_id
    )
    session.add(other_workspace)
    await session.commit()
    other_role = executor_role.model_copy(update={"workspace_id": other_workspace.id})

    for call in (internal_router.get_submission, internal_router.delete_submission):
        with pytest.raises(HTTPException) as exc_info:
            await call(role=other_role, session=session, submission_id=submission_id)
        assert exc_info.value.status_code == 404
    assert await _stored(session, submission_id)
