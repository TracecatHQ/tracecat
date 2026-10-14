"""Form config CRUD, validation, rendering, views, user search and intake."""

import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import (
    create_form,
    create_org_user,
    create_organization_with_workspace,
    create_submission,
    create_workflow,
    publish_workflow,
    use_session,
)
from tracecat.auth import credentials
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import (
    EDITOR_SCOPES,
    INTAKE_SCOPES,
    SERVICE_PRINCIPAL_SCOPES,
    VIEWER_SCOPES,
)
from tracecat.db.models import (
    Form,
    FormView,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workflow,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.forms import internal_router
from tracecat.forms import router as forms_router
from tracecat.forms import service as forms_service
from tracecat.forms.enums import FormErrorCode, FormIssueCode, FormStatus
from tracecat.forms.exceptions import FormError
from tracecat.forms.schemas import (
    FormConfig,
    FormCreate,
    FormUpdate,
    FormValidateRequest,
)
from tracecat.forms.service import FormsIntakeService, FormsService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

# JSONB orders object keys by length and then bytewise: a, mm, zeta.
EXPECTS: dict[str, dict[str, Any]] = {
    "zeta": {"type": "str", "description": "Why you need it"},
    "mm": {"type": 'enum["mba", "mbp"]'},
    "a": {"type": "bool", "default": False},
}
AUTHOR_ORDER = ["zeta", "mm", "a"]
USER_EXPECTS: dict[str, dict[str, Any]] = {"recipient": {"type": "dict"}}
USER_LAYOUT = [{"field": "recipient", "widget": "user"}]


@pytest.fixture
async def workflow(session: AsyncSession, svc_workspace: Workspace) -> Workflow:
    return await create_workflow(session, svc_workspace.id)


@pytest.fixture
async def published_workflow(
    session: AsyncSession, builder_role: Role, workflow: Workflow
) -> Workflow:
    await publish_workflow(session, builder_role, workflow, EXPECTS)
    return workflow


@pytest.fixture
def service(session: AsyncSession, builder_role: Role) -> FormsService:
    return FormsService(session, builder_role)


def _names(blocks: list[Any]) -> list[str]:
    return [block.name for block in blocks if block.type == "field"]


def _new_form(
    workflow_id: uuid.UUID,
    *,
    status: FormStatus = FormStatus.OFFLINE,
    config: dict[str, Any] | None = None,
) -> FormCreate:
    return FormCreate(
        workflow_id=WorkflowUUID.new(workflow_id),
        status=status,
        config=FormConfig.model_validate(config) if config is not None else None,
    )


async def _error(call: Awaitable[Any]) -> FormError:
    with pytest.raises(FormError) as exc_info:
        await call
    return exc_info.value


# ----- Create, update, read -----


async def test_create_form(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    other_workspace = await create_organization_with_workspace(session, name="other")
    elsewhere = await create_workflow(session, other_workspace.id)
    for workflow_id in (uuid.uuid4(), elsewhere.id):
        error = await _error(service.create_form(_new_form(workflow_id)))
        assert error.code is FormErrorCode.NOT_FOUND
    bad = {"layout": [{"field": "missing"}]}
    error = await _error(
        service.create_form(_new_form(published_workflow.id, config=bad))
    )
    assert error.code is FormErrorCode.FORM_CONFIG_INVALID
    assert [issue.code for issue in error.issues or []] == [FormIssueCode.UNKNOWN_FIELD]

    # Nothing was saved, so a valid form goes through. A second one clashes.
    form = await service.create_form(_new_form(published_workflow.id))
    error = await _error(service.create_form(_new_form(published_workflow.id)))
    assert error.code is FormErrorCode.ALREADY_EXISTS

    read = await service.read_form(form)
    assert read.status is FormStatus.OFFLINE
    assert read.workflow_id == WorkflowUUID.new(published_workflow.id).short()
    assert (read.workflow_title, read.workflow_version) == ("Provision laptop", 1)
    assert (read.config, read.issues) == (FormConfig(), [])
    assert read.url.endswith(f"/forms/{form.id}")
    assert read.counts is not None
    assert (read.counts.views, read.counts.submissions) == (0, 0)


async def test_form_on_unpublished_workflow_uses_draft_inputs_and_stays_offline(
    session: AsyncSession, service: FormsService, svc_workspace: Workspace
) -> None:
    draft = await create_workflow(session, svc_workspace.id, draft_expects=EXPECTS)
    online = _new_form(draft.id, status=FormStatus.ONLINE)
    error = await _error(service.create_form(online))
    assert error.code is FormErrorCode.FORM_WORKFLOW_UNPUBLISHED

    form = await service.create_form(_new_form(draft.id))

    assert (await service.read_form(form)).workflow_version is None
    validation = await service.validate_config(form, FormConfig())
    assert _names(validation.preview.blocks) == AUTHOR_ORDER
    error = await _error(
        service.update_form(form, FormUpdate(status=FormStatus.ONLINE))
    )
    assert error.code is FormErrorCode.FORM_WORKFLOW_UNPUBLISHED


async def test_a_new_offline_form_shows_the_issues_of_its_default_config(
    session: AsyncSession, service: FormsService, builder_role: Role, workflow: Workflow
) -> None:
    # A required object input has no default widget, so the default config
    # has an error. The form is still created, offline, to be fixed in the editor.
    await publish_workflow(session, builder_role, workflow, USER_EXPECTS)
    online = _new_form(workflow.id, status=FormStatus.ONLINE)
    assert (await _error(service.create_form(online))).code is (
        FormErrorCode.FORM_CONFIG_INVALID
    )

    form = await service.create_form(_new_form(workflow.id))

    read = await service.read_form(form)
    assert read.status is FormStatus.OFFLINE
    assert [issue.code for issue in read.issues] == [FormIssueCode.NO_WIDGET_FOR_TYPE]
    error = await _error(
        service.update_form(form, FormUpdate(status=FormStatus.ONLINE))
    )
    assert error.code is FormErrorCode.FORM_CONFIG_INVALID
    fixed = FormConfig.model_validate({"layout": USER_LAYOUT})
    form = await service.update_form(
        form, FormUpdate(status=FormStatus.ONLINE, config=fixed)
    )
    assert form.status == FormStatus.ONLINE.value


async def test_update_replaces_the_config_and_saves_nothing_invalid(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    form = await service.create_form(
        _new_form(
            published_workflow.id,
            config={"title": "Laptop request", "success_message": "IT will follow up"},
        )
    )

    form = await service.update_form(form, FormUpdate(status=FormStatus.ONLINE))
    assert (await service.read_form(form)).config.title == "Laptop request"

    new_config = FormConfig.model_validate({"layout": [{"heading": "Details"}]})
    form = await service.update_form(form, FormUpdate(config=new_config))
    assert (form.title, form.success_message, form.status) == (None, None, "online")
    assert form.layout == [{"heading": "Details"}]

    bad = FormConfig.model_validate({"layout": [{"field": "zeta", "widget": "toggle"}]})
    params = FormUpdate(status=FormStatus.OFFLINE, config=bad)
    error = await _error(service.update_form(form, params))
    assert error.code is FormErrorCode.FORM_CONFIG_INVALID
    await session.refresh(form)
    assert (form.status, form.layout) == ("online", [{"heading": "Details"}])


async def test_form_cannot_go_online_once_its_config_stops_resolving(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    published_workflow: Workflow,
) -> None:
    config = {"layout": [{"field": "zeta"}]}
    form = await service.create_form(_new_form(published_workflow.id, config=config))
    # A later publish removes the input the layout names.
    other_inputs = {"other": {"type": "str"}}
    await publish_workflow(session, builder_role, published_workflow, other_inputs)

    read = await service.read_form(form)
    assert read.workflow_version == 2
    assert [issue.code for issue in read.issues] == [FormIssueCode.UNKNOWN_FIELD]
    error = await _error(
        service.update_form(form, FormUpdate(status=FormStatus.ONLINE))
    )
    assert error.code is FormErrorCode.FORM_CONFIG_INVALID
    # Taking a form offline is always allowed.
    form = await service.update_form(form, FormUpdate(status=FormStatus.OFFLINE))
    assert form.status == FormStatus.OFFLINE


async def test_config_reads_back_in_the_authors_key_order(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    layout = [
        {"notice": "Reviewed within 2 business days.", "tone": "warning"},
        {"field": "zeta", "label": "Justification", "widget": "long_text"},
    ]
    form = await service.create_form(
        _new_form(published_workflow.id, config={"layout": layout})
    )
    form_id = form.id
    session.expire_all()

    reloaded = await service.get_form(form_id)
    assert reloaded is not None
    dumped = (await service.read_form(reloaded)).model_dump(mode="json")["config"]

    assert dumped == {"layout": layout}
    assert [list(entry) for entry in dumped["layout"]] == [
        ["notice", "tone"],
        ["field", "label", "widget"],
    ]


async def test_upsert_creates_the_form_then_updates_it(
    session: AsyncSession, svc_workspace: Workspace, published_workflow: Workflow
) -> None:
    executor = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )
    workflow_id = WorkflowUUID.new(published_workflow.id)

    async def upsert(params: FormUpdate) -> Any:
        return await internal_router.upsert_form(
            role=executor, session=session, workflow_id=workflow_id, params=params
        )

    created = await upsert(FormUpdate(config=FormConfig(title="Laptop request")))
    updated = await upsert(FormUpdate(status=FormStatus.ONLINE))

    assert (created.status, created.workflow_id) == ("offline", workflow_id.short())
    assert (updated.id, updated.status) == (created.id, "online")
    # A status-only update keeps the config.
    assert updated.config.title == "Laptop request"
    assert len((await session.scalars(select(Form))).all()) == 1


async def test_sync_form_writes_without_checks_and_clears_with_no_config(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    workflow_id = published_workflow.id
    await service.sync_form(workflow_id, None, FormStatus.ONLINE)
    assert await service.get_form_by_workflow_id(workflow_id) is None

    # A pulled block that does not resolve is stored anyway.
    pulled = FormConfig.model_validate({"layout": [{"field": "not_an_input"}]})
    await service.sync_form(workflow_id, pulled, FormStatus.ONLINE)
    form = await service.get_form_by_workflow_id(workflow_id)
    assert form is not None
    assert (form.status, form.layout) == ("online", [{"field": "not_an_input"}])

    await create_submission(session, form)
    await service.sync_form(workflow_id, None, FormStatus.ONLINE)
    assert (form.status, form.layout) == ("offline", None)
    # The caller owns the transaction.
    await session.commit()
    await session.refresh(form)
    counts = (await service.read_form(form)).counts
    assert counts is not None and counts.submissions == 1


# ----- List -----


async def test_list_forms_shows_requesters_online_forms_without_workflow_fields(
    session: AsyncSession,
    builder_role: Role,
    intake_role: Role,
    svc_workspace: Workspace,
) -> None:
    online_workflow = await create_workflow(
        session, svc_workspace.id, title="Triage phishing report"
    )
    offline_workflow = await create_workflow(
        session, svc_workspace.id, title="Offboard employee", description=""
    )
    online = await create_form(session, online_workflow, title="Report phishing")
    offline = await create_form(session, offline_workflow, status=FormStatus.OFFLINE)
    await create_submission(session, online)
    elsewhere = await create_organization_with_workspace(session, name="other")
    await create_form(session, await create_workflow(session, elsewhere.id))

    builder_page = await FormsService(session, builder_role).list_forms(PageParams())
    intake_page = await FormsService(session, intake_role).list_forms(PageParams())

    by_id = {form.id: form for form in builder_page.items}
    assert set(by_id) == {online.id, offline.id}
    assert by_id[online.id].title == "Report phishing"
    assert by_id[online.id].workflow_title == "Triage phishing report"
    assert by_id[online.id].workflow_id == WorkflowUUID.new(online_workflow.id).short()
    assert by_id[online.id].submission_count == 1
    assert by_id[online.id].last_submitted_at is not None
    # Title and description fall back to the workflow's.
    assert (by_id[offline.id].title, by_id[offline.id].description) == (
        "Offboard employee",
        None,
    )
    assert by_id[offline.id].submission_count == 0

    [listed] = intake_page.items
    assert (listed.id, listed.title) == (online.id, "Report phishing")
    # A requester is not told which workflow a form runs, nor its counts.
    assert listed.workflow_id is None
    assert listed.workflow_title is None
    assert listed.submission_count is None
    assert listed.last_submitted_at is None


async def test_list_forms_is_cursor_paginated(
    session: AsyncSession, service: FormsService, svc_workspace: Workspace
) -> None:
    form_ids = set()
    for index in range(3):
        workflow = await create_workflow(session, svc_workspace.id, title=f"W{index}")
        form_ids.add((await create_form(session, workflow)).id)

    first = await service.list_forms(PageParams(limit=2))
    assert first.has_more and first.next_cursor is not None
    second = await service.list_forms(PageParams(limit=2, cursor=first.next_cursor))

    assert not second.has_more
    assert {form.id for form in [*first.items, *second.items]} == form_ids


# ----- Validate, render and the requester boundary -----


async def test_validate_resolves_an_unsaved_config_in_the_authors_order(
    session: AsyncSession, builder_role: Role, published_workflow: Workflow
) -> None:
    form = await create_form(session, published_workflow, status=FormStatus.OFFLINE)
    config = {"title": "Laptop request", "layout": [{"field": "missing"}]}

    async def validate(config: dict[str, Any]) -> Any:
        return await forms_router.validate_form(
            role=builder_role,
            session=session,
            form_id=form.id,
            params=FormValidateRequest.model_validate({"config": config}),
        )

    valid = await validate({})
    invalid = await validate(config)

    assert (valid.valid, valid.issues) == (True, [])
    assert valid.preview.title == "Provision laptop"
    assert valid.preview.description == "Request a new laptop from IT."
    assert valid.preview.definition_version == 1
    # The definition restores the order JSONB lost.
    assert _names(valid.preview.blocks) == AUTHOR_ORDER
    assert not invalid.valid
    assert [issue.code for issue in invalid.issues] == [FormIssueCode.UNKNOWN_FIELD]
    assert invalid.preview.title == "Laptop request"
    await session.refresh(form)
    assert form.layout is None


@pytest.mark.parametrize(
    ("scopes", "status_code"),
    [(INTAKE_SCOPES, 404), (VIEWER_SCOPES, 200), (EDITOR_SCOPES, 200)],
    ids=["intake", "viewer", "editor"],
)
async def test_only_builders_and_readers_reach_the_config_routes(
    session: AsyncSession,
    intake_role: Role,
    published_workflow: Workflow,
    scopes: frozenset[str],
    status_code: int,
) -> None:
    """A requester gets the same 404 for a real form as for an unknown ID."""
    form = await create_form(session, published_workflow, status=FormStatus.OFFLINE)
    role = intake_role.model_copy(update={"scopes": scopes})
    routes = (
        lambda form_id: forms_router.get_form(
            role=role, session=session, form_id=form_id
        ),
        lambda form_id: forms_router.validate_form(
            role=role,
            session=session,
            form_id=form_id,
            params=FormValidateRequest(config=FormConfig()),
        ),
    )

    for route in routes:
        for form_id, expected in ((form.id, status_code), (uuid.uuid4(), 404)):
            try:
                await route(form_id)
                got = 200
            except HTTPException as e:
                assert e.detail == "Form not found"
                got = e.status_code
            assert got == expected


async def test_render_resolves_an_online_form(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    form = await create_form(
        session,
        published_workflow,
        title="Laptop request",
        layout=[{"field": "a", "hidden": True}],
    )

    preview = await service.render_form(form)

    assert (preview.title, preview.definition_version) == ("Laptop request", 1)
    assert _names(preview.blocks) == ["zeta", "mm"]
    zeta = preview.blocks[0]
    assert zeta.type == "field"
    assert (zeta.help, zeta.required) == ("Why you need it", True)


@pytest.mark.parametrize(
    ("status", "layout", "published", "code"),
    [
        (FormStatus.OFFLINE, None, True, FormErrorCode.FORM_OFFLINE),
        (FormStatus.ONLINE, None, False, FormErrorCode.FORM_UNAVAILABLE),
        (FormStatus.ONLINE, [{"field": "x"}], True, FormErrorCode.FORM_UNAVAILABLE),
    ],
)
async def test_render_refuses_offline_and_unavailable_forms(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    workflow: Workflow,
    status: FormStatus,
    layout: list[dict[str, Any]] | None,
    published: bool,
    code: FormErrorCode,
) -> None:
    if published:
        await publish_workflow(session, builder_role, workflow, EXPECTS)
    form = await create_form(session, workflow, status=status, layout=layout)

    error = await _error(service.render_form(form))

    assert error.code is code
    # Requesters are not told what is wrong with the config.
    assert error.issues is None


# ----- Views -----


async def _views(session: AsyncSession, form: Form) -> list[FormView]:
    result = await session.execute(select(FormView).where(FormView.form_id == form.id))
    return list(result.scalars().all())


async def test_views_count_once_per_user_within_the_window(
    session: AsyncSession,
    service: FormsService,
    intake_role: Role,
    colleague_role: Role,
    requester: User,
    published_workflow: Workflow,
) -> None:
    form = await create_form(session, published_workflow)

    await service.record_view(form)
    await service.record_view(form)
    [view] = await _views(session, form)
    assert view.user_id == requester.id

    await FormsService(session, colleague_role).record_view(form)
    await session.execute(
        update(FormView)
        .where(FormView.id == view.id)
        .values(created_at=datetime.now(UTC) - timedelta(minutes=6))
    )
    await session.commit()
    await FormsService(session, intake_role).record_view(form)

    counts = (await service.read_form(form)).counts
    assert counts is not None
    assert (counts.views, counts.submissions) == (3, 0)
    # Counts need form:submission:read.
    assert (await FormsService(session, intake_role).read_form(form)).counts is None


async def test_service_accounts_and_failures_record_no_view(
    session: AsyncSession,
    api_key_role: Role,
    builder_role: Role,
    published_workflow: Workflow,
) -> None:
    form = await create_form(session, published_workflow)
    form_id = form.id
    # No user row behind this ID, so the insert violates a foreign key.
    ghost = builder_role.model_copy(update={"user_id": uuid.uuid4()})

    await FormsService(session, api_key_role).record_view(form)
    await FormsService(session, ghost).record_view(form)

    rows = await session.execute(select(FormView.id).where(FormView.form_id == form_id))
    assert rows.first() is None


# ----- User search -----


async def test_user_search_needs_a_form_the_caller_could_fill_in(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    intake_role: Role,
    requester: User,
    workflow: Workflow,
) -> None:
    intake = FormsService(session, intake_role)
    form = await create_form(session, workflow, status=FormStatus.OFFLINE)

    async def code(svc: FormsService) -> FormErrorCode:
        return (await _error(svc.search_users(form, PageParams()))).code

    # An editor previews unsaved and offline forms; a requester cannot.
    found = await service.search_users(form, PageParams())
    assert [user.user_id for user in found.items] == [requester.id]
    assert await code(intake) is FormErrorCode.FORM_OFFLINE
    form.status = FormStatus.ONLINE.value
    await session.commit()
    assert await code(intake) is FormErrorCode.FORM_UNAVAILABLE
    await publish_workflow(session, builder_role, workflow, EXPECTS)
    assert await code(intake) is FormErrorCode.NOT_FOUND

    await publish_workflow(session, builder_role, workflow, USER_EXPECTS)
    form.layout = USER_LAYOUT
    await session.commit()
    found = await intake.search_users(form, PageParams())
    assert [user.user_id for user in found.items] == [requester.id]


async def test_user_search_lists_active_members_of_the_organization(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    requester: User,
    svc_workspace: Workspace,
    workflow: Workflow,
) -> None:
    await publish_workflow(session, builder_role, workflow, USER_EXPECTS)
    form = await create_form(session, workflow, layout=USER_LAYOUT)
    org_id = svc_workspace.organization_id
    arjun = await create_org_user(
        session, org_id, email="arjun.rao@example.com", first_name="Arjun"
    )
    await create_org_user(session, org_id, email="gone@example.com", is_active=False)
    other = await create_organization_with_workspace(session, name="other")
    await create_org_user(session, other.organization_id, email="out@example.com")

    async def emails(search: str | None = None, **page: Any) -> list[str]:
        found = await service.search_users(form, PageParams(**page), search=search)
        return [user.email for user in found.items]

    assert await emails() == ["arjun.rao@example.com", "maya.okafor@example.com"]
    assert await emails("rao") == ["arjun.rao@example.com"]
    assert await emails("MAYA.") == ["maya.okafor@example.com"]
    # Wildcards in the search text are matched literally.
    assert await emails("%") == []
    first = await service.search_users(form, PageParams(limit=1))
    assert [user.user_id for user in first.items] == [arjun.id]
    assert await emails(limit=1, cursor=first.next_cursor) == [requester.email]


# ----- Intake: forms across the caller's workspaces -----


@pytest.fixture
def org_role(svc_workspace: Workspace, requester: User) -> Role:
    """An organization-level user role, as ``OrgUserRole`` produces it."""
    return Role(
        type="user",
        organization_id=svc_workspace.organization_id,
        user_id=requester.id,
        service_id="tracecat-api",
        # Lets the catalog see every workspace of the organization, so the
        # per-workspace form:read check is what decides.
        scopes=frozenset({"org:workspace:read"}),
    )


@pytest.fixture
def rbac(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Run the real scope query, and the RLS-bypass reads, on the test session."""
    for module, name in (
        (credentials, "get_async_session_auth_context_manager"),
        (forms_service, "get_async_session_bypass_rls_context_manager"),
    ):
        monkeypatch.setattr(module, name, use_session(session))
    credentials._compute_effective_scopes_cached.cache_clear()


async def _grant(
    session: AsyncSession,
    user_id: uuid.UUID | None,
    workspace: Workspace,
    *names: str,
    org_wide: bool = False,
) -> UserRoleAssignment:
    """Give a user a custom role holding these scopes in one workspace, or in
    every workspace of its organization."""
    assert user_id is not None
    org_id = workspace.organization_id
    role = DBRole(name=f"Synthetic {uuid.uuid4().hex[:8]}", organization_id=org_id)
    session.add(role)
    await session.flush()
    for name in names:
        resource, _, action = name.rpartition(":")
        scope = await session.scalar(
            select(Scope).where(Scope.organization_id == org_id, Scope.name == name)
        )
        if scope is None:
            scope = Scope(
                name=name,
                resource=resource,
                action=action,
                source=ScopeSource.CUSTOM,
                organization_id=org_id,
            )
            session.add(scope)
            await session.flush()
        session.add(RoleScope(role_id=role.id, scope_id=scope.id))
    assignment = UserRoleAssignment(
        organization_id=org_id,
        user_id=user_id,
        workspace_id=None if org_wide else workspace.id,
        role_id=role.id,
    )
    session.add(assignment)
    await session.commit()
    credentials._compute_effective_scopes_cached.cache_clear()
    return assignment


async def _form_in(
    session: AsyncSession,
    workspace: Workspace,
    title: str,
    status: FormStatus = FormStatus.ONLINE,
) -> Form:
    workflow = await create_workflow(session, workspace.id, title=f"{title} workflow")
    return await create_form(session, workflow, status=status, title=title)


async def _locate(session: AsyncSession, role: Role, form_id: uuid.UUID) -> int:
    try:
        await forms_router.locate_intake_form(
            role=role, session=session, form_id=form_id
        )
    except HTTPException as e:
        return e.status_code
    return 200


@pytest.mark.usefixtures("rbac")
@pytest.mark.parametrize(
    ("names", "status_code"),
    [
        (("form:read",), 200),
        (("form:*",), 200),
        (("form:submit", "workflow:read"), 404),
        (("form:submission:read",), 404),
    ],
)
async def test_locate_needs_form_read_in_the_forms_workspace(
    session: AsyncSession,
    org_role: Role,
    svc_workspace: Workspace,
    names: tuple[str, ...],
    status_code: int,
) -> None:
    # Offline is not an access failure: the render route answers 409 for it.
    form = await _form_in(session, svc_workspace, "Offboarding", FormStatus.OFFLINE)
    await _grant(session, org_role.user_id, svc_workspace, *names)

    assert await _locate(session, org_role, form.id) == status_code


@pytest.mark.usefixtures("rbac")
async def test_locate_needs_access_to_the_forms_workspace(
    session: AsyncSession, org_role: Role, svc_workspace: Workspace
) -> None:
    # An organization role that holds an org scope grants form:read in every
    # workspace, but no workspace membership.
    form = await _form_in(session, svc_workspace, "Offboarding")
    await _grant(
        session,
        org_role.user_id,
        svc_workspace,
        "org:member:read",
        "form:read",
        org_wide=True,
    )
    member = org_role.model_copy(update={"scopes": frozenset({"org:member:read"})})

    assert (
        await FormsIntakeService(session, member).list_forms(PageParams())
    ).items == []
    assert await _locate(session, member, form.id) == 404


@pytest.mark.usefixtures("rbac")
async def test_intake_shows_what_the_callers_own_assignments_allow(
    session: AsyncSession,
    org_role: Role,
    colleague_role: Role,
    svc_workspace: Workspace,
) -> None:
    second = Workspace(name="second", organization_id=svc_workspace.organization_id)
    session.add(second)
    foreign_workspace = await create_organization_with_workspace(session, name="x")
    visible = await _form_in(session, svc_workspace, "Laptop request")
    await _form_in(session, svc_workspace, "Offboarding", FormStatus.OFFLINE)
    other = await _form_in(session, second, "Access request")
    foreign = await _form_in(session, foreign_workspace, "Foreign form")
    mine = await create_submission(session, visible, user_id=org_role.user_id)
    await create_submission(session, visible, user_id=colleague_role.user_id)
    await create_submission(session, visible)
    await create_submission(session, other, user_id=org_role.user_id)
    # Another user's assignment, and other scopes, grant the caller nothing.
    await _grant(session, colleague_role.user_id, svc_workspace, "form:read")
    elsewhere = await _grant(
        session, org_role.user_id, second, "workflow:read", "form:submit"
    )
    intake = FormsIntakeService(session, org_role)
    assert await _locate(session, org_role, visible.id) == 404
    assert (await intake.list_forms(PageParams())).items == []

    assignment = await _grant(session, org_role.user_id, svc_workspace, "form:read")

    location = await forms_router.locate_intake_form(
        role=org_role, session=session, form_id=visible.id
    )
    assert location.model_dump() == {
        "form_id": visible.id,
        "workspace_id": svc_workspace.id,
        "workspace_name": svc_workspace.name,
    }
    for form_id in (other.id, foreign.id, uuid.uuid4()):
        assert await _locate(session, org_role, form_id) == 404
    api_key = org_role.model_copy(update={"type": "service_account", "user_id": None})
    assert await _locate(session, api_key, visible.id) == 404
    [listed] = (await intake.list_forms(PageParams())).items
    assert listed.model_dump() == {
        "id": visible.id,
        "workspace_id": svc_workspace.id,
        "workspace_name": svc_workspace.name,
        "title": "Laptop request",
        "description": "Request a new laptop from IT.",
    }
    [item] = (await intake.list_submissions(PageParams())).items
    # Requesters never get run fields or answers from this route.
    assert item.model_dump(exclude={"submitted_at"}) == {
        "id": mine.id,
        "reference": f"FRM-{mine.id.hex[:8].upper()}",
        "form_id": visible.id,
        "form_title": "Laptop request",
        "workspace_id": svc_workspace.id,
        "workspace_name": svc_workspace.name,
    }

    # The catalog spans every workspace the caller reads forms in.
    await session.delete(elsewhere)
    await _grant(session, org_role.user_id, second, "form:read")
    page_one = await intake.list_forms(PageParams(limit=1))
    page_two = await intake.list_forms(PageParams(limit=1, cursor=page_one.next_cursor))
    assert {form.id for form in [*page_one.items, *page_two.items]} == {
        visible.id,
        other.id,
    }
    await session.delete(assignment)
    await session.commit()
    credentials._compute_effective_scopes_cached.cache_clear()
    assert await _locate(session, org_role, visible.id) == 404
