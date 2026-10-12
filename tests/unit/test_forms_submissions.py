"""Tests for submitting a form and for reading and deleting submissions."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client, WorkflowExecutionStatus

from tests._forms_utils import (
    create_form,
    create_org_user,
    create_organization_with_workspace,
    create_workflow,
    dsl_with_inputs,
    publish_workflow,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import require_action_scope
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.models import (
    Form,
    FormEvent,
    FormSubmission,
    ServiceAccount,
    User,
    Workflow,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError
from tracecat.forms import router as forms_router
from tracecat.forms.enums import (
    FormErrorCode,
    FormEventType,
    FormFieldErrorCode,
    FormStatus,
)
from tracecat.forms.exceptions import FormError
from tracecat.forms.resolver import resolve_form
from tracecat.forms.schemas import (
    FormConfig,
    FormFieldError,
    FormSubmissionCreate,
    FormSubmissionReceipt,
)
from tracecat.forms.service import submission_reference
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.forms.values import check_values, visible_fields
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

INTAKE_SCOPES = frozenset({"form:read", "form:submit"})
READER_SCOPES = INTAKE_SCOPES | {"form:submission:read"}

EXPECTS: dict[str, dict[str, Any]] = {
    "model": {"type": 'enum["mba", "mbp"]'},
    "justification": {"type": "str"},
    "quantity": {"type": "int", "default": 1},
    "for_someone_else": {"type": "bool", "default": False},
    "recipient": {"type": "dict | None", "default": None},
    "needed_by": {"type": "str | None", "default": None},
    "priority": {"type": "str", "default": "normal"},
    "requester": {"type": "dict | None"},
    "reference": {"type": "str"},
}
LAYOUT: list[dict[str, Any]] = [
    {
        "field": "model",
        "widget": "cards",
        "options": [{"value": "mbp", "label": "MacBook Pro 14"}],
    },
    {"field": "justification", "widget": "long_text"},
    {"field": "for_someone_else"},
    {
        "field": "recipient",
        "widget": "user",
        "visible_if": {"field": "for_someone_else", "equals": True},
    },
    {"field": "needed_by", "widget": "date"},
    {"field": "priority", "hidden": True},
    {"field": "requester", "autofill": "requester"},
    {"field": "reference", "autofill": "submission.id"},
]
VALID_VALUES: dict[str, Any] = {"model": "mbp", "justification": "Mine broke"}


@dataclass
class FakeTemporal:
    """Stands in for ``WorkflowExecutionsService.connect``."""

    connect: AsyncMock
    service: AsyncMock

    @property
    def run_role(self) -> Role:
        self.connect.assert_awaited_once()
        assert self.connect.await_args is not None
        return self.connect.await_args.kwargs["role"]

    @property
    def start_kwargs(self) -> dict[str, Any]:
        start = self.service.create_workflow_execution_wait_for_start
        start.assert_awaited_once()
        assert start.await_args is not None
        return start.await_args.kwargs


@pytest.fixture
def temporal(monkeypatch: pytest.MonkeyPatch) -> FakeTemporal:
    service = AsyncMock(spec=WorkflowExecutionsService)
    service.query_executions.return_value = []
    connect = AsyncMock(return_value=service)
    monkeypatch.setattr(WorkflowExecutionsService, "connect", connect)
    return FakeTemporal(connect=connect, service=service)


@pytest.fixture
async def requester(session: AsyncSession, svc_workspace: Workspace) -> User:
    return await create_org_user(
        session,
        svc_workspace.organization_id,
        email="maya.okafor@example.com",
        first_name="Maya",
        last_name="Okafor",
    )


@pytest.fixture
def intake_role(svc_workspace: Workspace, requester: User) -> Role:
    """A requester holding nothing but the Intake only preset's scopes."""
    return Role(
        type="user",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=requester.id,
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )


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
    service: FormSubmissionsService, form: Form, values: dict[str, Any]
) -> FormSubmissionReceipt:
    return await service.submit(form, FormSubmissionCreate(values=values))


async def _submit_error(
    service: FormSubmissionsService, form: Form, values: dict[str, Any]
) -> FormError:
    with pytest.raises(FormError) as exc_info:
        await _submit(service, form, values)
    return exc_info.value


def _field_errors(error: FormError) -> dict[str, FormFieldErrorCode]:
    assert error.code is FormErrorCode.FORM_VALIDATION_ERROR
    return {item.field: item.code for item in error.errors or []}


async def _stored(session: AsyncSession, model: type[Any]) -> list[Any]:
    return list((await session.execute(select(model))).scalars().all())


# ----- Value checks without a database -----


def _blocks(
    layout: list[dict[str, Any]] | None = None,
    expects: dict[str, Any] | None = None,
) -> list[Any]:
    result = resolve_form(
        FormConfig.model_validate({"layout": layout or LAYOUT}),
        dsl_with_inputs(expects or EXPECTS).entrypoint.expects or {},
    )
    assert not result.has_errors
    return result.blocks


def test_conditional_field_follows_its_controller() -> None:
    blocks = _blocks()

    def names(values: dict[str, Any]) -> list[str]:
        return [field.name for field in visible_fields(blocks, values)]

    assert "recipient" not in names({})
    assert "recipient" not in names({"for_someone_else": False})
    assert "recipient" in names({"for_someone_else": True})
    # A truthy value of another type does not satisfy the rule.
    assert "recipient" not in names({"for_someone_else": 1})
    assert "recipient" not in names({"for_someone_else": "true"})


def test_missing_controller_counts_as_its_default() -> None:
    blocks = _blocks(
        layout=[
            {"field": "express"},
            {"field": "reason", "visible_if": {"field": "express", "equals": True}},
        ],
        expects={
            "express": {"type": "bool", "default": True},
            "reason": {"type": "str", "default": ""},
        },
    )

    assert [f.name for f in visible_fields(blocks, {})] == ["express", "reason"]
    assert [f.name for f in visible_fields(blocks, {"express": False})] == ["express"]


def test_check_values_drops_values_of_hidden_conditional_fields() -> None:
    checked = check_values(_blocks(), VALID_VALUES | {"recipient": str(uuid.uuid4())})

    assert checked.errors == []
    assert checked.values == VALID_VALUES


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, {"model": "required", "justification": "required"}),
        (
            {"model": "", "justification": None},
            {
                "model": "required",
                "justification": "required",
            },
        ),
        (VALID_VALUES | {"model": "dell"}, {"model": "invalid_option"}),
        (VALID_VALUES | {"model": ["mbp"]}, {"model": "invalid_value"}),
        (VALID_VALUES | {"model": 3}, {"model": "invalid_option"}),
        (VALID_VALUES | {"needed_by": "next week"}, {"needed_by": "invalid_date"}),
        (VALID_VALUES | {"needed_by": "2026-02-30"}, {"needed_by": "invalid_date"}),
        (
            VALID_VALUES | {"needed_by": "2026-02-03T10:00:00"},
            {"needed_by": "invalid_date"},
        ),
        (VALID_VALUES | {"nope": 1}, {"nope": "unknown_field"}),
        # Hidden and autofilled inputs are not fields of the form.
        (VALID_VALUES | {"priority": "urgent"}, {"priority": "unknown_field"}),
        (VALID_VALUES | {"requester": {}}, {"requester": "unknown_field"}),
    ],
)
def test_check_values_errors(values: dict[str, Any], expected: dict[str, str]) -> None:
    checked = check_values(_blocks(), values)

    assert {error.field: error.code.value for error in checked.errors} == expected


def test_check_values_accepts_dates_toggles_and_lists() -> None:
    blocks = _blocks(
        layout=[
            {"field": "day", "widget": "date"},
            {"field": "tags", "widget": "multi_select", "options": [{"value": "a"}]},
        ],
        expects={
            "day": {"type": "str"},
            "at": {"type": "datetime"},
            "agree": {"type": "bool"},
            "tags": {"type": "list[str]"},
            "notes": {"type": "str", "default": ""},
        },
    )
    values = {
        "day": "2026-02-03",
        "at": "2026-02-03T10:00:00Z",
        "agree": False,
        "tags": ["a"],
        "notes": "",
    }

    checked = check_values(blocks, values)

    assert checked.errors == []
    # False is an answer. An empty optional value is left to its default.
    assert checked.values == {k: v for k, v in values.items() if k != "notes"}
    assert check_values(blocks, values | {"tags": ["a", "b"]}).errors == [
        FormFieldError(
            field="tags",
            code=FormFieldErrorCode.INVALID_OPTION,
            message="Choose one of the options",
        )
    ]
    assert [e.code for e in check_values(blocks, values | {"tags": "a"}).errors] == [
        FormFieldErrorCode.INVALID_VALUE
    ]
    assert [e.field for e in check_values(blocks, values | {"at": "soon"}).errors] == [
        "at"
    ]


# ----- Submit -----


async def test_submit_stores_the_answers_and_starts_the_run(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    requester: User,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID_VALUES)

    [submission] = await _stored(session, FormSubmission)
    assert receipt.id == submission.id
    assert receipt.reference == submission_reference(submission.id)
    assert receipt.reference.startswith("FRM-")
    assert receipt.submitted_at == submission.created_at
    assert receipt.success_message == "IT will follow up by email."
    # Requesters never receive run fields.
    assert set(receipt.model_dump()) == {
        "id",
        "reference",
        "submitted_at",
        "success_message",
    }

    expected_answers = {
        "model": "mbp",
        "justification": "Mine broke",
        "quantity": 1,
        "for_someone_else": False,
        "recipient": None,
        "needed_by": None,
        "priority": "normal",
        "requester": {
            "user_id": str(requester.id),
            "email": "maya.okafor@example.com",
            "first_name": "Maya",
            "last_name": "Okafor",
        },
        "reference": str(submission.id),
    }
    assert submission.answers == expected_answers
    assert submission.form_id == form.id
    assert submission.submitted_by_user_id == requester.id
    assert submission.submitted_by_service_account_id is None
    assert submission.definition_version == 1

    [event] = await _stored(session, FormEvent)
    assert event.type == FormEventType.FORM_SUBMITTED
    assert event.submission_id == submission.id
    assert event.user_id == requester.id

    start = temporal.start_kwargs
    assert start["trigger_type"] is TriggerType.FORM
    assert start["wf_id"] == WorkflowUUID.new(form.workflow_id)
    assert start["wf_exec_id"] == submission.wf_exec_id
    assert start["wf_exec_id"].startswith(WorkflowUUID.new(form.workflow_id).short())
    # The run gets exactly what was stored.
    assert start["payload"] == expected_answers
    assert start["registry_lock"] is None
    assert start["memo"] == {
        "form_id": str(form.id),
        "form_submission_id": str(submission.id),
    }
    # Inputs reach the run in the author's order, read from the definition.
    assert list(start["dsl"].entrypoint.expects) == list(EXPECTS)


async def test_submit_drops_values_of_fields_the_requester_cannot_see(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    requester: User,
    form: Form,
) -> None:
    await _submit(service, form, VALID_VALUES | {"recipient": str(requester.id)})

    [submission] = await _stored(session, FormSubmission)
    assert submission.answers["for_someone_else"] is False
    assert submission.answers["recipient"] is None


async def test_submit_expands_user_ids(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
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

    await _submit(
        service,
        form,
        VALID_VALUES | {"for_someone_else": True, "recipient": str(arjun.id)},
    )

    [submission] = await _stored(session, FormSubmission)
    assert submission.answers["recipient"] == {
        "user_id": str(arjun.id),
        "email": "arjun.rao@example.com",
        "first_name": "Arjun",
        "last_name": "Rao",
    }


async def test_submit_expands_a_list_of_users(
    session: AsyncSession,
    intake_role: Role,
    temporal: FakeTemporal,
    requester: User,
    svc_workspace: Workspace,
) -> None:
    workflow = await create_workflow(session, svc_workspace.id, title="Grant access")
    await publish_workflow(
        session, intake_role, workflow, {"approvers": {"type": "list[dict]"}}
    )
    form = await create_form(
        session, workflow, layout=[{"field": "approvers", "widget": "user"}]
    )
    arjun = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    service = FormSubmissionsService(session, intake_role)

    await _submit(service, form, {"approvers": [str(arjun.id), str(requester.id)]})

    [submission] = await _stored(session, FormSubmission)
    assert [user["email"] for user in submission.answers["approvers"]] == [
        "arjun.rao@example.com",
        "maya.okafor@example.com",
    ]


@pytest.mark.parametrize("kind", ["unknown", "inactive", "other_org", "not_an_id"])
async def test_submit_rejects_users_outside_the_organization(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
    form: Form,
    kind: str,
) -> None:
    match kind:
        case "inactive":
            user = await create_org_user(
                session,
                svc_workspace.organization_id,
                email="left.company@example.com",
                is_active=False,
            )
            recipient: Any = str(user.id)
        case "other_org":
            other = await create_organization_with_workspace(session, name="other")
            user = await create_org_user(
                session, other.organization_id, email="outsider@example.com"
            )
            recipient = str(user.id)
        case "not_an_id":
            recipient = {"email": "arjun.rao@example.com"}
        case _:
            recipient = str(uuid.uuid4())

    error = await _submit_error(
        service, form, VALID_VALUES | {"for_someone_else": True, "recipient": recipient}
    )

    assert _field_errors(error) == {"recipient": FormFieldErrorCode.INVALID_USER}
    assert await _stored(session, FormSubmission) == []
    temporal.connect.assert_not_awaited()


async def test_submit_reports_every_invalid_field(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    error = await _submit_error(
        service,
        form,
        {"model": "dell", "needed_by": "tomorrow", "surprise": True},
    )

    assert _field_errors(error) == {
        "surprise": FormFieldErrorCode.UNKNOWN_FIELD,
        "model": FormFieldErrorCode.INVALID_OPTION,
        "justification": FormFieldErrorCode.REQUIRED,
        "needed_by": FormFieldErrorCode.INVALID_DATE,
    }
    assert await _stored(session, FormSubmission) == []
    assert await _stored(session, FormEvent) == []
    temporal.connect.assert_not_awaited()


async def test_submit_maps_input_schema_errors_to_fields(
    session: AsyncSession,
    intake_role: Role,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
) -> None:
    workflow = await create_workflow(session, svc_workspace.id, title="Order")
    await publish_workflow(
        session,
        intake_role,
        workflow,
        {"quantity": {"type": "int"}, "note": {"type": "str"}},
    )
    form = await create_form(session, workflow)
    service = FormSubmissionsService(session, intake_role)

    error = await _submit_error(service, form, {"quantity": "many", "note": "ok"})

    assert _field_errors(error) == {"quantity": FormFieldErrorCode.INVALID_VALUE}
    [field_error] = error.errors or []
    # The message describes the problem without repeating the answer.
    assert "many" not in field_error.message
    temporal.connect.assert_not_awaited()


async def test_users_cannot_supply_autofilled_inputs(
    service: FormSubmissionsService, temporal: FakeTemporal, form: Form
) -> None:
    error = await _submit_error(
        service,
        form,
        VALID_VALUES | {"requester": {"email": "someone.else@example.com"}},
    )

    assert _field_errors(error) == {"requester": FormFieldErrorCode.UNKNOWN_FIELD}


# ----- API keys -----


@pytest.fixture
async def api_key_role(session: AsyncSession, svc_workspace: Workspace) -> Role:
    account = ServiceAccount(
        organization_id=svc_workspace.organization_id,
        workspace_id=svc_workspace.id,
        name="Intake bot",
    )
    session.add(account)
    await session.commit()
    return Role(
        type="service_account",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        service_account_id=account.id,
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )


async def test_api_key_submission_autofills_from_the_callers_values_or_null(
    session: AsyncSession, api_key_role: Role, temporal: FakeTemporal, form: Form
) -> None:
    service = FormSubmissionsService(session, api_key_role)
    on_behalf_of = {"email": "maya.okafor@example.com"}

    await _submit(service, form, VALID_VALUES | {"requester": on_behalf_of})
    await _submit(service, form, VALID_VALUES | {"reference": "caller-chosen"})

    submissions = {
        submission.answers["requester"] is None: submission
        for submission in await _stored(session, FormSubmission)
    }
    with_value, without_value = submissions[False], submissions[True]
    assert with_value.answers["requester"] == on_behalf_of
    assert with_value.submitted_by_user_id is None
    assert with_value.submitted_by_service_account_id == api_key_role.service_account_id
    # The submission ID always comes from the server.
    assert without_value.answers["reference"] == str(without_value.id)

    events = await _stored(session, FormEvent)
    assert {event.service_account_id for event in events} == {
        api_key_role.service_account_id
    }
    # No requester to attribute the run to.
    assert temporal.connect.await_args is not None
    run_role = temporal.connect.await_args.kwargs["role"]
    assert run_role.type == "service"
    assert run_role.user_id is None


async def test_api_key_submission_fails_on_a_required_autofill_it_did_not_send(
    session: AsyncSession,
    api_key_role: Role,
    temporal: FakeTemporal,
    svc_workspace: Workspace,
) -> None:
    workflow = await create_workflow(session, svc_workspace.id, title="Strict")
    await publish_workflow(session, api_key_role, workflow, {"email": {"type": "str"}})
    form = await create_form(
        session, workflow, layout=[{"field": "email", "autofill": "requester.email"}]
    )
    service = FormSubmissionsService(session, api_key_role)

    error = await _submit_error(service, form, {})

    assert _field_errors(error) == {"email": FormFieldErrorCode.INVALID_VALUE}

    await _submit(service, form, {"email": "maya.okafor@example.com"})
    [submission] = await _stored(session, FormSubmission)
    assert submission.answers == {"email": "maya.okafor@example.com"}


# ----- Form state -----


async def test_submit_to_an_offline_form_is_refused(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    form.status = FormStatus.OFFLINE.value
    await session.commit()

    error = await _submit_error(service, form, VALID_VALUES)

    assert error.code is FormErrorCode.FORM_OFFLINE
    temporal.connect.assert_not_awaited()


async def test_submit_to_a_form_whose_config_stopped_resolving_is_refused(
    session: AsyncSession,
    service: FormSubmissionsService,
    intake_role: Role,
    temporal: FakeTemporal,
    workflow: Workflow,
    form: Form,
) -> None:
    # A later publish drops inputs the layout names.
    await publish_workflow(session, intake_role, workflow, {"model": {"type": "str"}})

    error = await _submit_error(service, form, {"model": "mbp"})

    assert error.code is FormErrorCode.FORM_UNAVAILABLE
    assert error.issues is None
    temporal.connect.assert_not_awaited()


async def test_submit_with_a_stale_definition_version_is_refused(
    session: AsyncSession,
    service: FormSubmissionsService,
    intake_role: Role,
    temporal: FakeTemporal,
    workflow: Workflow,
    form: Form,
) -> None:
    await publish_workflow(session, intake_role, workflow, EXPECTS)

    with pytest.raises(FormError) as exc_info:
        await service.submit(
            form, FormSubmissionCreate(values=VALID_VALUES, definition_version=1)
        )
    assert exc_info.value.code is FormErrorCode.FORM_CHANGED
    temporal.connect.assert_not_awaited()

    await service.submit(
        form, FormSubmissionCreate(values=VALID_VALUES, definition_version=2)
    )
    [submission] = await _stored(session, FormSubmission)
    assert submission.definition_version == 2


async def test_start_failure_rolls_back_the_submission(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    start = temporal.service.create_workflow_execution_wait_for_start
    start.side_effect = TimeoutError("Timed out while dispatching workflow")

    error = await _submit_error(service, form, VALID_VALUES)

    assert error.code is FormErrorCode.WORKFLOW_START_FAILED
    start.assert_awaited_once()
    assert await _stored(session, FormSubmission) == []
    assert await _stored(session, FormEvent) == []


async def test_connect_failure_rolls_back_the_submission(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    temporal.connect.side_effect = RuntimeError("Temporal is unreachable")

    error = await _submit_error(service, form, VALID_VALUES)

    assert error.code is FormErrorCode.WORKFLOW_START_FAILED
    assert await _stored(session, FormSubmission) == []


# ----- Run identity -----


async def test_intake_only_requester_starts_a_run_that_can_execute_actions(
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    intake_role: Role,
    requester: User,
    form: Form,
) -> None:
    await _submit(service, form, VALID_VALUES)
    run_role = temporal.run_role

    assert run_role.type == "service"
    assert run_role.service_id == "tracecat-runner"
    assert run_role.user_id == requester.id
    assert run_role.workspace_id == intake_role.workspace_id
    assert run_role.organization_id == intake_role.organization_id
    assert run_role.scopes == SERVICE_PRINCIPAL_SCOPES["tracecat-runner"]

    # The executor checks action scopes against the run role. The requester's
    # own role fails that check for every action; the run role passes it.
    token = ctx_role.set(intake_role)
    try:
        with pytest.raises(ScopeDeniedError):
            require_action_scope("core.http_request")
    finally:
        ctx_role.reset(token)
    token = ctx_role.set(run_role)
    try:
        require_action_scope("core.http_request")
        require_action_scope("tools.example.list_users")
    finally:
        ctx_role.reset(token)


async def test_run_role_records_the_requester_as_triggered_by(
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    requester: User,
    form: Form,
) -> None:
    await _submit(service, form, VALID_VALUES)
    start = temporal.start_kwargs

    # Start a run with the real executions service and the role the submit
    # flow built, and read what it sends to Temporal.
    client = Mock(spec=Client)
    client.start_workflow = AsyncMock(return_value=Mock())
    executions = WorkflowExecutionsService(client=client, role=temporal.run_role)
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

    client.start_workflow.assert_awaited_once()
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
    assert kwargs["id"] == start["wf_exec_id"]
    assert kwargs["memo"] == start["memo"]
    run_args = client.start_workflow.await_args.args[1]
    assert run_args.role.user_id == requester.id
    assert run_args.role.service_id == "tracecat-runner"


# ----- Reading and deleting submissions -----


@pytest.fixture
def reader_role(intake_role: Role) -> Role:
    return intake_role.model_copy(update={"scopes": READER_SCOPES})


@pytest.fixture
async def colleague_role(
    session: AsyncSession, svc_workspace: Workspace, intake_role: Role
) -> Role:
    colleague = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    return intake_role.model_copy(update={"user_id": colleague.id})


async def test_requester_reads_their_own_submission_without_run_fields(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    colleague_role: Role,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID_VALUES)
    temporal.connect.reset_mock()

    own = await service.read_submission(form, receipt.id)
    someone_elses = await FormSubmissionsService(
        session, colleague_role
    ).read_submission(form, receipt.id)

    assert own is not None
    assert own.id == receipt.id
    assert own.reference == receipt.reference
    assert own.answers["justification"] == "Mine broke"
    assert own.submitted_by is not None
    assert own.submitted_by.email == "maya.okafor@example.com"
    assert own.wf_exec_id is None
    assert own.run_status is None
    # No status lookup is made for a requester.
    temporal.connect.assert_not_awaited()
    assert someone_elses is None
    assert await service.read_submission(form, uuid.uuid4()) is None


async def test_submission_readers_see_any_submission_with_run_fields(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    colleague_role: Role,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID_VALUES)
    [stored] = await _stored(session, FormSubmission)
    temporal.service.query_executions.return_value = [
        Mock(id=stored.wf_exec_id, status=WorkflowExecutionStatus.COMPLETED)
    ]
    reader = FormSubmissionsService(
        session, colleague_role.model_copy(update={"scopes": READER_SCOPES})
    )

    submission = await reader.read_submission(form, receipt.id)

    assert submission is not None
    assert submission.wf_exec_id == stored.wf_exec_id
    assert submission.run_status == "COMPLETED"


async def test_list_submissions_filters_by_date_and_attaches_run_status(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    reader_role: Role,
    api_key_role: Role,
    form: Form,
) -> None:
    old = await _submit(service, form, VALID_VALUES | {"justification": "Old"})
    recent = await _submit(
        FormSubmissionsService(session, api_key_role),
        form,
        VALID_VALUES | {"justification": "Recent"},
    )
    now = datetime.now(UTC)
    await session.execute(
        update(FormSubmission)
        .where(FormSubmission.id == old.id)
        .values(created_at=now - timedelta(days=10))
    )
    await session.commit()
    stored = {s.id: s for s in await _stored(session, FormSubmission)}
    temporal.service.query_executions.reset_mock()
    temporal.service.query_executions.return_value = [
        Mock(id=stored[old.id].wf_exec_id, status=WorkflowExecutionStatus.FAILED),
        Mock(id=stored[recent.id].wf_exec_id, status=WorkflowExecutionStatus.RUNNING),
    ]
    reader = FormSubmissionsService(session, reader_role)

    everything = await reader.list_submissions(form, PageParams())
    last_week = await reader.list_submissions(
        form, PageParams(), submitted_after=now - timedelta(days=7)
    )
    before_last_week = await reader.list_submissions(
        form, PageParams(), submitted_before=now - timedelta(days=7)
    )

    assert [s.id for s in everything.items] == [recent.id, old.id]
    assert [s.run_status for s in everything.items] == ["RUNNING", "FAILED"]
    assert [s.wf_exec_id for s in everything.items] == [
        stored[recent.id].wf_exec_id,
        stored[old.id].wf_exec_id,
    ]
    by_key, by_user = everything.items
    assert by_key.submitted_by is not None
    assert (by_key.submitted_by.type, by_key.submitted_by.name) == (
        "service_account",
        "Intake bot",
    )
    assert by_user.submitted_by is not None
    assert by_user.submitted_by.email == "maya.okafor@example.com"
    assert by_user.answers["justification"] == "Old"
    assert [s.id for s in last_week.items] == [recent.id]
    assert [s.id for s in before_last_week.items] == [old.id]
    # One visibility query per page, scoped to that page's runs.
    assert temporal.service.query_executions.await_count == 3
    first_query = temporal.service.query_executions.await_args_list[0].kwargs["query"]
    assert first_query == (
        f"WorkflowId IN ('{stored[recent.id].wf_exec_id}', "
        f"'{stored[old.id].wf_exec_id}')"
    )


async def test_list_submissions_survives_a_failed_status_lookup(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    reader_role: Role,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID_VALUES)
    temporal.service.query_executions.side_effect = RuntimeError("Temporal is down")

    page = await FormSubmissionsService(session, reader_role).list_submissions(
        form, PageParams()
    )

    [submission] = page.items
    assert submission.id == receipt.id
    assert submission.run_status is None
    assert submission.wf_exec_id is not None


async def test_delete_submission_keeps_the_activity_event(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID_VALUES)
    submission = await service.get_submission(form, receipt.id)
    assert submission is not None

    await service.delete_submission(submission)

    assert await _stored(session, FormSubmission) == []
    assert await service.get_submission(form, receipt.id) is None
    [event] = await _stored(session, FormEvent)
    assert event.type == FormEventType.FORM_SUBMITTED
    assert event.submission_id is None


async def test_submissions_are_scoped_to_their_form(
    session: AsyncSession,
    service: FormSubmissionsService,
    temporal: FakeTemporal,
    intake_role: Role,
    svc_workspace: Workspace,
    form: Form,
) -> None:
    receipt = await _submit(service, form, VALID_VALUES)
    other_workflow = await create_workflow(session, svc_workspace.id, title="Other")
    await publish_workflow(session, intake_role, other_workflow, {})
    other_form = await create_form(session, other_workflow)

    assert await service.read_submission(other_form, receipt.id) is None
    assert await service.get_submission(other_form, receipt.id) is None


# ----- HTTP error mapping -----


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        (FormErrorCode.FORM_CONFIG_INVALID, 422),
        (FormErrorCode.FORM_WORKFLOW_UNPUBLISHED, 409),
        (FormErrorCode.FORM_OFFLINE, 409),
        (FormErrorCode.FORM_UNAVAILABLE, 409),
        (FormErrorCode.FORM_CHANGED, 409),
        (FormErrorCode.FORM_VALIDATION_ERROR, 422),
        (FormErrorCode.WORKFLOW_START_FAILED, 503),
        (FormErrorCode.FILE_FIELD_INVALID, 422),
        (FormErrorCode.FILE_INVALID, 400),
        (FormErrorCode.FILE_TYPE_NOT_ALLOWED, 415),
        (FormErrorCode.FILE_TOO_LARGE, 413),
        (FormErrorCode.FILE_LIMIT_REACHED, 409),
        (FormErrorCode.FILE_UPLOAD_FAILED, 503),
    ],
)
def test_form_errors_map_to_http_statuses(
    code: FormErrorCode, status_code: int
) -> None:
    field_error = FormFieldError(
        field="model",
        code=FormFieldErrorCode.REQUIRED,
        message="This field is required",
    )

    response = forms_router.form_http_error(
        FormError(code, "Something is wrong", errors=[field_error])
    )

    assert response.status_code == status_code
    assert response.detail == {
        "code": code.value,
        "message": "Something is wrong",
        "errors": [
            {"field": "model", "code": "required", "message": "This field is required"}
        ],
        "issues": None,
    }


def test_every_form_error_code_has_an_http_status() -> None:
    for code in FormErrorCode:
        assert forms_router.form_http_error(FormError(code, "x")).status_code >= 400
