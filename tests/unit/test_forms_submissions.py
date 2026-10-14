"""Submitting a form, the run it starts, and reading and deleting submissions."""

import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from tests._forms_utils import (
    FakeTemporal,
    create_form,
    create_org_user,
    create_organization_with_workspace,
    create_workflow,
    publish_workflow,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import require_action_scope
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.models import Form, FormSubmission, User, Workflow, Workspace
from tracecat.exceptions import ScopeDeniedError
from tracecat.forms import internal_router
from tracecat.forms.enums import FormErrorCode, FormFieldErrorCode, FormStatus
from tracecat.forms.exceptions import FormError
from tracecat.forms.schemas import FormSubmissionCreate, FormSubmissionReceipt
from tracecat.forms.service import FormsService
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

EXPECTS: dict[str, dict[str, Any]] = {
    "model": {"type": 'enum["mba", "mbp"]'},
    "justification": {"type": "str"},
    "quantity": {"type": "int", "default": 1},
    "for_someone_else": {"type": "bool", "default": False},
    "recipient": {"type": "dict | None", "default": None},
    "approvers": {"type": "list[dict]", "default": []},
    "needed_by": {"type": "str | None", "default": None},
    "priority": {"type": "str", "default": "normal"},
    "requester": {"type": "dict | None"},
    "reference": {"type": "str"},
}
LAYOUT: list[dict[str, Any]] = [
    {"field": "model", "widget": "cards"},
    {"field": "justification", "widget": "long_text"},
    {"field": "for_someone_else"},
    {
        "field": "recipient",
        "widget": "user",
        "visible_if": {"field": "for_someone_else", "equals": True},
    },
    {"field": "approvers", "widget": "user"},
    {"field": "needed_by", "widget": "date"},
    {"field": "priority", "hidden": True},
    {"field": "requester", "autofill": "requester"},
    {"field": "reference", "autofill": "submission.id"},
]
VALID: dict[str, Any] = {"model": "mbp", "justification": "Mine broke"}
SHOWN_DEFAULTS: dict[str, Any] = {
    "quantity": 1,
    "for_someone_else": False,
    "recipient": None,
    "approvers": [],
    "needed_by": None,
}


@pytest.fixture
async def workflow(
    session: AsyncSession, intake_role: Role, svc_workspace: Workspace
) -> Workflow:
    workflow = await create_workflow(session, svc_workspace.id)
    await publish_workflow(session, intake_role, workflow, EXPECTS)
    return workflow


@pytest.fixture
async def form(session: AsyncSession, workflow: Workflow) -> Form:
    form = await create_form(session, workflow, layout=LAYOUT)
    form.success_message = "IT will follow up by email."
    await session.commit()
    return form


@pytest.fixture
def service(session: AsyncSession, intake_role: Role) -> FormSubmissionsService:
    return FormSubmissionsService(session, intake_role)


async def _submit(
    service: FormSubmissionsService, form: Form, values: dict[str, Any], **kwargs: Any
) -> FormSubmissionReceipt:
    return await service.submit(form, FormSubmissionCreate(values=values, **kwargs))


async def _error(call: Awaitable[Any]) -> FormError:
    with pytest.raises(FormError) as exc_info:
        await call
    return exc_info.value


async def _field_errors(call: Awaitable[Any]) -> dict[str, FormFieldErrorCode]:
    error = await _error(call)
    assert error.code is FormErrorCode.FORM_VALIDATION_ERROR
    return {item.field: item.code for item in error.errors or []}


async def _stored(session: AsyncSession) -> list[FormSubmission]:
    return list((await session.execute(select(FormSubmission))).scalars().all())


# ----- Submit -----


async def test_submit_stores_the_answers_and_starts_the_run(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    requester: User,
    svc_workspace: Workspace,
    form: Form,
) -> None:
    arjun = await create_org_user(
        session,
        svc_workspace.organization_id,
        email="arjun.rao@example.com",
        first_name="Arjun",
        last_name="Rao",
    )
    # recipient is not visible, so its value is dropped.
    values = VALID | {
        "recipient": str(arjun.id),
        "approvers": [str(arjun.id), str(requester.id)],
    }

    receipt = await _submit(service, form, values)

    [submission] = await _stored(session)
    # Requesters never receive run fields.
    assert receipt.model_dump() == {
        "id": submission.id,
        "reference": f"FRM-{submission.id.hex[:8].upper()}",
        "submitted_at": submission.created_at,
        "success_message": "IT will follow up by email.",
    }
    user = {"first_name": "Maya", "last_name": "Okafor"}
    maya = {"user_id": str(requester.id), "email": requester.email} | user
    expected = (
        VALID
        | SHOWN_DEFAULTS
        | {
            "approvers": [
                {
                    "user_id": str(arjun.id),
                    "email": "arjun.rao@example.com",
                    "first_name": "Arjun",
                    "last_name": "Rao",
                },
                maya,
            ],
            "priority": "normal",
            "requester": maya,
            "reference": str(submission.id),
        }
    )
    assert submission.answers == expected
    assert (submission.form_id, submission.definition_version) == (form.id, 1)
    assert submission.submitted_by_user_id == requester.id
    assert submission.submitted_by_service_account_id is None

    start = temporal.start_kwargs
    workflow_id = WorkflowUUID.new(form.workflow_id)
    assert start["trigger_type"] is TriggerType.FORM
    assert start["wf_id"] == workflow_id
    assert start["wf_exec_id"] == submission.wf_exec_id
    assert start["wf_exec_id"].startswith(workflow_id.short())
    # The run gets exactly what was stored.
    assert start["payload"] == expected
    assert start["registry_lock"] is None
    assert start["memo"] == {
        "form_id": str(form.id),
        "form_submission_id": str(submission.id),
    }
    # Inputs reach the run in the author's order, read from the definition.
    assert list(start["dsl"].entrypoint.expects) == list(EXPECTS)


@pytest.mark.parametrize("kind", ["unknown", "inactive", "other_org", "not_an_id"])
async def test_submit_rejects_users_outside_the_organization(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
    form: Form,
    kind: str,
) -> None:
    recipient: Any = str(uuid.uuid4())
    if kind == "inactive":
        user = await create_org_user(
            session,
            svc_workspace.organization_id,
            email="gone@example.com",
            is_active=False,
        )
        recipient = str(user.id)
    elif kind == "other_org":
        other = await create_organization_with_workspace(session, name="other")
        user = await create_org_user(
            session, other.organization_id, email="outsider@example.com"
        )
        recipient = str(user.id)
    elif kind == "not_an_id":
        recipient = {"email": "arjun.rao@example.com"}

    values = VALID | {"for_someone_else": True, "recipient": recipient}
    errors = await _field_errors(_submit(service, form, values))

    assert errors == {"recipient": FormFieldErrorCode.INVALID_USER}
    assert await _stored(session) == []
    temporal.connect.assert_not_awaited()


async def test_submit_reports_every_invalid_field(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    values = {"model": "dell", "needed_by": "tomorrow", "surprise": True}

    errors = await _field_errors(_submit(service, form, values))

    assert errors == {
        "surprise": FormFieldErrorCode.UNKNOWN_FIELD,
        "model": FormFieldErrorCode.INVALID_OPTION,
        "justification": FormFieldErrorCode.REQUIRED,
        "needed_by": FormFieldErrorCode.INVALID_DATE,
    }
    assert await _stored(session) == []
    temporal.connect.assert_not_awaited()


async def test_submit_maps_input_schema_errors_to_fields(
    session: AsyncSession,
    intake_role: Role,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
) -> None:
    workflow = await create_workflow(session, svc_workspace.id, title="Order")
    expects = {"quantity": {"type": "int"}, "note": {"type": "str"}}
    await publish_workflow(session, intake_role, workflow, expects)
    form = await create_form(session, workflow)
    service = FormSubmissionsService(session, intake_role)

    error = await _error(_submit(service, form, {"quantity": "many", "note": "ok"}))

    [field_error] = error.errors or []
    assert (field_error.field, field_error.code) == (
        "quantity",
        FormFieldErrorCode.INVALID_VALUE,
    )
    # The message describes the problem without repeating the answer.
    assert "many" not in field_error.message
    temporal.connect.assert_not_awaited()


@pytest.mark.parametrize("caller", ["user", "api_key"])
@pytest.mark.parametrize(
    "extra",
    [{"requester": {"email": "someone.else@example.com"}}, {"reference": "chosen"}],
    ids=["requester", "submission_id"],
)
async def test_callers_cannot_supply_autofilled_inputs(
    session: AsyncSession,
    intake_role: Role,
    api_key_role: Role,
    temporal: FakeTemporal,
    form: Form,
    caller: str,
    extra: dict[str, Any],
) -> None:
    """A key holder cannot name the requester either: it would be unverified."""
    role = intake_role if caller == "user" else api_key_role
    service = FormSubmissionsService(session, role)

    errors = await _field_errors(_submit(service, form, VALID | extra))

    assert errors == dict.fromkeys(extra, FormFieldErrorCode.UNKNOWN_FIELD)
    assert await _stored(session) == []


async def test_api_key_submission_has_no_requester(
    session: AsyncSession,
    api_key_role: Role,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
    form: Form,
) -> None:
    await _submit(FormSubmissionsService(session, api_key_role), form, VALID)

    [submission] = await _stored(session)
    assert submission.answers["requester"] is None
    assert submission.submitted_by_user_id is None
    assert submission.submitted_by_service_account_id == api_key_role.service_account_id
    # The submission ID always comes from the server.
    assert submission.answers["reference"] == str(submission.id)
    assert temporal.run_role.user_id is None

    # A required requester value cannot be filled in for an API key.
    workflow = await create_workflow(session, svc_workspace.id, title="Strict")
    await publish_workflow(session, api_key_role, workflow, {"email": {"type": "str"}})
    strict = await create_form(
        session, workflow, layout=[{"field": "email", "autofill": "requester.email"}]
    )
    service = FormSubmissionsService(session, api_key_role)
    errors = await _field_errors(_submit(service, strict, {}))
    assert errors == {"email": FormFieldErrorCode.INVALID_VALUE}


# ----- Form state and the run start -----


async def test_submit_refuses_offline_changed_and_unavailable_forms(
    session: AsyncSession,
    service: FormSubmissionsService,
    intake_role: Role,
    temporal: FakeTemporal,
    workflow: Workflow,
    form: Form,
) -> None:
    await publish_workflow(session, intake_role, workflow, EXPECTS)
    error = await _error(_submit(service, form, VALID, definition_version=1))
    assert error.code is FormErrorCode.FORM_CHANGED
    await _submit(service, form, VALID, definition_version=2)
    [submission] = await _stored(session)
    assert submission.definition_version == 2

    # A later publish drops inputs the layout names.
    await publish_workflow(session, intake_role, workflow, {"model": {"type": "str"}})
    error = await _error(_submit(service, form, {"model": "mbp"}))
    assert (error.code, error.issues) == (FormErrorCode.FORM_UNAVAILABLE, None)

    form.status = FormStatus.OFFLINE.value
    await session.commit()
    error = await _error(_submit(service, form, VALID))
    assert error.code is FormErrorCode.FORM_OFFLINE
    assert len(await _stored(session)) == 1


async def test_submit_refuses_answers_to_a_config_that_changed_since_render(
    session: AsyncSession,
    service: FormSubmissionsService,
    intake_role: Role,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    forms = FormsService(session, intake_role)
    rendered = await forms.render_form(form)
    # The builder makes a field conditional; the workflow stays the same.
    when_for_someone_else = {
        "visible_if": {"field": "for_someone_else", "equals": True}
    }
    form.layout = [
        entry | when_for_someone_else if entry["field"] == "needed_by" else entry
        for entry in LAYOUT
    ]
    await session.commit()

    # The old page's answer would be dropped as no longer visible.
    stale = _submit(
        service,
        form,
        VALID | {"needed_by": "2026-03-01"},
        config_version=rendered.config_version,
    )
    assert (await _error(stale)).code is FormErrorCode.FORM_CHANGED
    assert await _stored(session) == []

    current = await forms.render_form(form)
    assert current.config_version != rendered.config_version
    await _submit(service, form, VALID, config_version=current.config_version)
    assert len(await _stored(session)) == 1


async def test_submission_is_committed_before_the_run_starts(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    monkeypatch: pytest.MonkeyPatch,
    form: Form,
) -> None:
    """The run's first action may read the submission, so it must exist."""
    calls: list[str] = []
    commit = session.commit

    async def recording_commit() -> None:
        calls.append("commit")
        await commit()

    async def recording_start(**kwargs: Any) -> None:
        calls.append("start")
        # Nothing is left waiting to be written when the run starts.
        assert not session.new
        assert not session.dirty

    monkeypatch.setattr(session, "commit", recording_commit)
    temporal.start.side_effect = recording_start

    receipt = await _submit(service, form, VALID)

    assert "commit" in calls[: calls.index("start")]
    [submission] = await _stored(session)
    assert submission.id == receipt.id


@pytest.mark.parametrize(
    ("connect_error", "start_error", "kept"),
    [
        (RuntimeError("Temporal is unreachable"), None, False),
        (TimeoutError("connect timed out"), None, False),
        (None, RuntimeError("Temporal rejected the request"), False),
        (None, RPCError("invalid", RPCStatusCode.INVALID_ARGUMENT, b""), False),
        # The run may exist, and it reads its submission by ID.
        (None, TimeoutError("Timed out while dispatching workflow"), True),
        (None, RPCError("deadline", RPCStatusCode.DEADLINE_EXCEEDED, b""), True),
    ],
)
async def test_start_failure_keeps_the_submission_only_if_the_run_may_exist(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
    connect_error: Exception | None,
    start_error: Exception | None,
    kept: bool,
) -> None:
    temporal.connect.side_effect = connect_error
    temporal.start.side_effect = start_error

    error = await _error(_submit(service, form, VALID))

    assert error.code is FormErrorCode.WORKFLOW_START_FAILED
    assert len(await _stored(session)) == int(kept)


async def test_the_run_executes_as_the_runner_on_behalf_of_the_requester(
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    intake_role: Role,
    requester: User,
    form: Form,
) -> None:
    await _submit(service, form, VALID)
    run_role = temporal.run_role
    start = temporal.start_kwargs

    assert (run_role.type, run_role.service_id) == ("service", "tracecat-runner")
    assert (run_role.workspace_id, run_role.organization_id) == (
        intake_role.workspace_id,
        intake_role.organization_id,
    )
    assert run_role.scopes == SERVICE_PRINCIPAL_SCOPES["tracecat-runner"]
    # The executor checks action scopes against the run role. The requester's
    # own role fails that check for every action; the run role passes it.
    for role, allowed in ((intake_role, False), (run_role, True)):
        token = ctx_role.set(role)
        try:
            if allowed:
                require_action_scope("core.http_request")
            else:
                with pytest.raises(ScopeDeniedError):
                    require_action_scope("core.http_request")
        finally:
            ctx_role.reset(token)

    # The real executions service records the requester as triggered-by.
    client = Mock(spec=Client)
    client.start_workflow = AsyncMock(return_value=Mock())
    executions = WorkflowExecutionsService(client=client, role=run_role)
    with patch.object(
        executions, "_resolve_execution_timeout", AsyncMock(return_value=None)
    ):
        await executions.create_workflow_execution_wait_for_start(
            dsl=start["dsl"],
            wf_id=start["wf_id"],
            payload=None,
            trigger_type=start["trigger_type"],
            wf_exec_id=start["wf_exec_id"],
            memo=start["memo"],
        )
    assert client.start_workflow.await_args is not None
    kwargs = client.start_workflow.await_args.kwargs
    attributes = {
        pair.key.name: pair.value
        for pair in kwargs["search_attributes"].search_attributes
    }
    assert attributes[TemporalSearchAttr.TRIGGERED_BY_USER_ID.value] == str(
        requester.id
    )
    assert attributes[TemporalSearchAttr.TRIGGER_TYPE.value] == "form"
    assert (kwargs["id"], kwargs["memo"]) == (start["wf_exec_id"], start["memo"])
    assert client.start_workflow.await_args.args[1].role.user_id == requester.id


# ----- Reading and deleting -----


async def test_requesters_read_only_their_own_submission_without_run_fields(
    session: AsyncSession,
    service: FormSubmissionsService,
    intake_role: Role,
    colleague_role: Role,
    temporal: FakeTemporal,
    workflow: Workflow,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID)
    temporal.connect.reset_mock()
    colleague = FormSubmissionsService(session, colleague_role)

    own = await service.read_submission(form, receipt.id)

    assert own is not None
    assert (own.id, own.reference) == (receipt.id, receipt.reference)
    # Only what the form showed them: no hidden default, no autofilled value.
    assert own.answers == VALID | SHOWN_DEFAULTS
    assert own.submitted_by is not None
    assert own.submitted_by.email == "maya.okafor@example.com"
    assert (own.definition_version, own.wf_exec_id, own.run_status) == (None,) * 3
    # No status lookup is made for a requester.
    temporal.connect.assert_not_awaited()
    assert await colleague.read_submission(form, receipt.id) is None
    assert await service.read_submission(form, uuid.uuid4()) is None

    # Once the form stops resolving, a requester sees none of the answers.
    await publish_workflow(session, intake_role, workflow, {"model": {"type": "str"}})
    own = await service.read_submission(form, receipt.id)
    assert own is not None and own.answers == {}


async def test_readers_list_any_submission_with_run_fields(
    session: AsyncSession,
    service: FormSubmissionsService,
    api_key_role: Role,
    reader_role: Role,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    old = await _submit(service, form, VALID | {"justification": "Old"})
    by_key = FormSubmissionsService(session, api_key_role)
    recent = await _submit(by_key, form, VALID | {"justification": "Recent"})
    now = datetime.now(UTC)
    await session.execute(
        update(FormSubmission)
        .where(FormSubmission.id == old.id)
        .values(created_at=now - timedelta(days=10))
    )
    await session.commit()
    exec_ids = {s.id: s.wf_exec_id for s in await _stored(session)}
    query = temporal.service.query_executions
    query.reset_mock()
    query.return_value = [
        Mock(id=exec_ids[old.id], status=WorkflowExecutionStatus.FAILED),
        Mock(id=exec_ids[recent.id], status=WorkflowExecutionStatus.RUNNING),
    ]
    reader = FormSubmissionsService(session, reader_role)

    everything = await reader.list_submissions(form, PageParams())
    week_ago = now - timedelta(days=7)
    last_week = await reader.list_submissions(
        form, PageParams(), submitted_after=week_ago
    )
    before = await reader.list_submissions(
        form, PageParams(), submitted_before=week_ago
    )
    one = await reader.read_submission(form, old.id)

    key_row, user_row = everything.items
    assert [key_row.id, user_row.id] == [recent.id, old.id]
    assert [key_row.run_status, user_row.run_status] == ["RUNNING", "FAILED"]
    assert [key_row.wf_exec_id, user_row.wf_exec_id] == [
        exec_ids[recent.id],
        exec_ids[old.id],
    ]
    assert key_row.submitted_by is not None
    assert (key_row.submitted_by.type, key_row.submitted_by.name) == (
        "service_account",
        "Intake bot",
    )
    assert user_row.submitted_by is not None
    assert user_row.submitted_by.email == "maya.okafor@example.com"
    assert user_row.answers["priority"] == "normal"
    assert [s.id for s in last_week.items] == [recent.id]
    assert [s.id for s in before.items] == [old.id]
    assert one is not None and one.definition_version == 1
    # One visibility query per page, scoped to that page's runs.
    assert query.await_args_list[0].kwargs["query"] == (
        f"WorkflowId IN ('{exec_ids[recent.id]}', '{exec_ids[old.id]}')"
    )

    query.side_effect = RuntimeError("Temporal is down")
    [survivor] = (await reader.list_submissions(form, PageParams(limit=1))).items
    assert (survivor.run_status, survivor.wf_exec_id) == (None, exec_ids[recent.id])


async def test_submissions_are_scoped_to_their_form_and_deleted_alone(
    session: AsyncSession,
    service: FormSubmissionsService,
    intake_role: Role,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID)
    other_workflow = await create_workflow(session, svc_workspace.id, title="Other")
    await publish_workflow(session, intake_role, other_workflow, {})
    other_form = await create_form(session, other_workflow)

    assert await service.read_submission(other_form, receipt.id) is None
    assert await service.get_submission(other_form, receipt.id) is None
    submission = await service.get_submission(form, receipt.id)
    assert submission is not None
    await service.delete_submission(submission)
    assert await _stored(session) == []


async def test_a_run_reads_and_deletes_its_submission_by_id_alone(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
    form: Form,
) -> None:
    """The internal routes for core.forms take a submission ID and no form."""
    receipt = await _submit(service, form, VALID)
    executor = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )
    other_workspace = Workspace(
        name="other-workspace", organization_id=svc_workspace.organization_id
    )
    session.add(other_workspace)
    await session.commit()
    outsider = executor.model_copy(update={"workspace_id": other_workspace.id})

    for route in (internal_router.get_submission, internal_router.delete_submission):
        with pytest.raises(HTTPException) as exc_info:
            await route(role=outsider, session=session, submission_id=receipt.id)
        assert exc_info.value.status_code == 404
    read = await internal_router.get_submission(
        role=executor, session=session, submission_id=receipt.id
    )
    assert (read.form_id, read.answers["model"]) == (form.id, "mbp")
    assert read.wf_exec_id is not None

    await internal_router.delete_submission(
        role=executor, session=session, submission_id=receipt.id
    )
    assert await _stored(session) == []
