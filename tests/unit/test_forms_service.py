"""Tests for form config CRUD, validation, rendering, activity and user search."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import (
    create_form,
    create_org_user,
    create_organization_with_workspace,
    create_workflow,
    publish_workflow,
)
from tracecat.auth.types import Role
from tracecat.db.models import (
    Form,
    FormEvent,
    FormSubmission,
    User,
    Workflow,
    Workspace,
)
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.forms.enums import (
    FormErrorCode,
    FormEventType,
    FormIssueCode,
    FormStatus,
)
from tracecat.forms.exceptions import FormError
from tracecat.forms.schemas import (
    FormConfig,
    FormCreate,
    FormFieldBlock,
    FormUpdate,
)
from tracecat.forms.service import FormsService, submission_reference
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

BUILDER_SCOPES = frozenset(
    {
        "form:read",
        "form:create",
        "form:update",
        "form:delete",
        "form:submission:read",
    }
)
INTAKE_SCOPES = frozenset({"form:read", "form:submit"})


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
def builder_role(svc_workspace: Workspace, requester: User) -> Role:
    return Role(
        type="user",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=requester.id,
        service_id="tracecat-api",
        scopes=BUILDER_SCOPES,
    )


@pytest.fixture
def intake_role(builder_role: Role) -> Role:
    return builder_role.model_copy(update={"scopes": INTAKE_SCOPES})


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


def _field_names(blocks: list[Any]) -> list[str]:
    return [block.name for block in blocks if isinstance(block, FormFieldBlock)]


def _new_form(
    workflow_id: uuid.UUID,
    *,
    status: FormStatus = FormStatus.OFFLINE,
    config: FormConfig | None = None,
) -> FormCreate:
    return FormCreate(
        workflow_id=WorkflowUUID.new(workflow_id), status=status, config=config
    )


# ----- Create -----


async def test_create_form_defaults_to_offline_with_no_layout(
    service: FormsService, published_workflow: Workflow
) -> None:
    form = await service.create_form(_new_form(published_workflow.id))

    assert form.status == FormStatus.OFFLINE
    assert form.layout is None
    read = await service.read_form(form)
    assert read.workflow_title == "Provision laptop"
    assert read.workflow_version == 1
    assert read.config == FormConfig()
    assert read.issues == []
    assert read.url.endswith(f"/forms/{form.id}")
    assert read.counts is not None
    assert (read.counts.views, read.counts.submissions) == (0, 0)


async def test_create_form_for_unknown_workflow_is_not_found(
    service: FormsService,
) -> None:
    with pytest.raises(TracecatNotFoundError):
        await service.create_form(_new_form(uuid.uuid4()))


async def test_create_form_for_workflow_in_another_workspace_is_not_found(
    session: AsyncSession, service: FormsService
) -> None:
    other_workspace = await create_organization_with_workspace(session, name="other")
    other_workflow = await create_workflow(session, other_workspace.id)

    with pytest.raises(TracecatNotFoundError):
        await service.create_form(_new_form(other_workflow.id))


async def test_second_form_for_a_workflow_conflicts(
    service: FormsService, published_workflow: Workflow
) -> None:
    await service.create_form(_new_form(published_workflow.id))

    with pytest.raises(TracecatConflictError):
        await service.create_form(_new_form(published_workflow.id))


async def test_create_form_with_invalid_config_returns_issues(
    service: FormsService, published_workflow: Workflow
) -> None:
    config = FormConfig.model_validate({"layout": [{"field": "missing"}]})

    with pytest.raises(FormError) as exc_info:
        await service.create_form(_new_form(published_workflow.id, config=config))

    assert exc_info.value.code is FormErrorCode.FORM_CONFIG_INVALID
    assert [issue.code for issue in exc_info.value.issues or []] == [
        FormIssueCode.UNKNOWN_FIELD
    ]
    assert await service.get_form_by_workflow_id(published_workflow.id) is None


async def test_form_on_unpublished_workflow_uses_draft_inputs_and_stays_offline(
    session: AsyncSession, service: FormsService, svc_workspace: Workspace
) -> None:
    draft = await create_workflow(session, svc_workspace.id, draft_expects=EXPECTS)

    with pytest.raises(FormError) as exc_info:
        await service.create_form(_new_form(draft.id, status=FormStatus.ONLINE))
    assert exc_info.value.code is FormErrorCode.FORM_WORKFLOW_UNPUBLISHED

    form = await service.create_form(_new_form(draft.id))
    read = await service.read_form(form)
    assert read.workflow_version is None
    validation = await service.validate_config(draft.id, FormConfig())
    assert _field_names(validation.preview.blocks) == AUTHOR_ORDER

    with pytest.raises(FormError) as exc_info:
        await service.update_form(form, FormUpdate(status=FormStatus.ONLINE))
    assert exc_info.value.code is FormErrorCode.FORM_WORKFLOW_UNPUBLISHED


# ----- Update -----


async def test_update_replaces_the_whole_config(
    service: FormsService, published_workflow: Workflow
) -> None:
    form = await service.create_form(
        _new_form(
            published_workflow.id,
            config=FormConfig.model_validate(
                {
                    "title": "Laptop request",
                    "success_message": "IT will follow up by email.",
                    "layout": [{"field": "zeta", "widget": "long_text"}],
                }
            ),
        )
    )

    new_config = FormConfig.model_validate({"layout": [{"heading": "Details"}]})
    form = await service.update_form(form, FormUpdate(config=new_config))

    assert form.title is None
    assert form.success_message is None
    assert form.layout == [{"heading": "Details"}]
    assert form.status == FormStatus.OFFLINE


async def test_update_status_keeps_the_config(
    service: FormsService, published_workflow: Workflow
) -> None:
    config = FormConfig.model_validate({"title": "Laptop request"})
    form = await service.create_form(_new_form(published_workflow.id, config=config))

    form = await service.update_form(form, FormUpdate(status=FormStatus.ONLINE))

    assert form.status == FormStatus.ONLINE
    assert (await service.read_form(form)).config == config


async def test_update_with_invalid_config_changes_nothing(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    form = await service.create_form(_new_form(published_workflow.id))
    bad_config = FormConfig.model_validate(
        {"layout": [{"field": "zeta", "widget": "toggle"}]}
    )

    with pytest.raises(FormError) as exc_info:
        await service.update_form(
            form, FormUpdate(status=FormStatus.ONLINE, config=bad_config)
        )

    assert exc_info.value.code is FormErrorCode.FORM_CONFIG_INVALID
    await session.refresh(form)
    assert form.status == FormStatus.OFFLINE
    assert form.layout is None


async def test_form_cannot_go_online_once_its_config_stops_resolving(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    published_workflow: Workflow,
) -> None:
    form = await service.create_form(
        _new_form(
            published_workflow.id,
            config=FormConfig.model_validate({"layout": [{"field": "zeta"}]}),
        )
    )
    # A later publish removes the input the layout names.
    await publish_workflow(
        session, builder_role, published_workflow, {"other": {"type": "str"}}
    )

    read = await service.read_form(form)
    assert read.workflow_version == 2
    assert [issue.code for issue in read.issues] == [FormIssueCode.UNKNOWN_FIELD]
    with pytest.raises(FormError) as exc_info:
        await service.update_form(form, FormUpdate(status=FormStatus.ONLINE))
    assert exc_info.value.code is FormErrorCode.FORM_CONFIG_INVALID

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
        _new_form(
            published_workflow.id,
            config=FormConfig.model_validate({"layout": layout}),
        )
    )
    form_id = form.id
    session.expire_all()

    reloaded = await service.get_form(form_id)
    assert reloaded is not None
    read = await service.read_form(reloaded)

    dumped = read.model_dump(mode="json")["config"]
    assert dumped == {"layout": layout}
    assert [list(entry) for entry in dumped["layout"]] == [
        ["notice", "tone"],
        ["field", "label", "widget"],
    ]


# ----- Delete -----


async def test_delete_form_removes_submissions_and_events(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    form = await create_form(session, published_workflow)
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        answers={},
        definition_version=1,
        wf_exec_id="wf_synthetic/exec_synthetic",
    )
    session.add(submission)
    await session.flush()
    session.add(
        FormEvent(
            workspace_id=form.workspace_id,
            form_id=form.id,
            type=FormEventType.FORM_SUBMITTED.value,
            submission_id=submission.id,
        )
    )
    await session.commit()

    await service.delete_form(form)

    for model in (Form, FormSubmission, FormEvent):
        assert (await session.execute(select(model))).first() is None
    assert (
        await session.execute(
            select(Workflow.id).where(Workflow.id == published_workflow.id)
        )
    ).first() is not None


# ----- List -----


async def test_list_forms_hides_offline_forms_from_callers_without_update(
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
    session.add(
        FormSubmission(
            workspace_id=online.workspace_id,
            form_id=online.id,
            answers={},
            definition_version=1,
            wf_exec_id="wf_synthetic/exec_synthetic",
        )
    )
    await session.commit()

    builder_page = await FormsService(session, builder_role).list_forms(PageParams())
    intake_page = await FormsService(session, intake_role).list_forms(PageParams())

    by_id = {form.id: form for form in builder_page.items}
    assert set(by_id) == {online.id, offline.id}
    assert by_id[online.id].title == "Report phishing"
    assert by_id[online.id].workflow_title == "Triage phishing report"
    assert by_id[online.id].workflow_id == WorkflowUUID.new(online_workflow.id)
    assert by_id[online.id].submission_count == 1
    assert by_id[online.id].last_submitted_at is not None
    # Title and description fall back to the workflow's.
    assert by_id[offline.id].title == "Offboard employee"
    assert by_id[offline.id].description is None
    assert by_id[offline.id].submission_count == 0

    assert [form.id for form in intake_page.items] == [online.id]
    assert intake_page.items[0].submission_count is None
    assert intake_page.items[0].last_submitted_at is None
    # A requester is not told which workflow a form runs.
    assert intake_page.items[0].title == "Report phishing"
    assert intake_page.items[0].workflow_id is None
    assert intake_page.items[0].workflow_title is None


async def test_list_forms_is_cursor_paginated(
    session: AsyncSession, service: FormsService, svc_workspace: Workspace
) -> None:
    form_ids = set()
    for index in range(3):
        workflow = await create_workflow(
            session, svc_workspace.id, title=f"Workflow {index}"
        )
        form_ids.add((await create_form(session, workflow)).id)

    first = await service.list_forms(PageParams(limit=2))
    assert first.has_more and first.next_cursor is not None
    second = await service.list_forms(PageParams(limit=2, cursor=first.next_cursor))

    assert not second.has_more
    assert {form.id for form in [*first.items, *second.items]} == form_ids


async def test_forms_are_scoped_to_the_workspace(
    session: AsyncSession, service: FormsService
) -> None:
    other_workspace = await create_organization_with_workspace(session, name="other")
    other_workflow = await create_workflow(session, other_workspace.id)
    other_form = await create_form(session, other_workflow)

    assert await service.get_form(other_form.id) is None
    assert (await service.list_forms(PageParams())).items == []


# ----- Validate and render -----


async def test_validate_previews_inputs_in_the_authors_order(
    service: FormsService, published_workflow: Workflow
) -> None:
    result = await service.validate_config(published_workflow.id, FormConfig())

    assert result.valid
    assert result.issues == []
    assert result.preview.title == "Provision laptop"
    assert result.preview.description == "Request a new laptop from IT."
    assert result.preview.definition_version == 1
    # The definition restores the order JSONB lost.
    assert _field_names(result.preview.blocks) == AUTHOR_ORDER


async def test_validate_reports_errors_without_saving(
    service: FormsService, published_workflow: Workflow
) -> None:
    config = FormConfig.model_validate(
        {"title": "Laptop request", "layout": [{"field": "missing"}]}
    )

    result = await service.validate_config(published_workflow.id, config)

    assert not result.valid
    assert [issue.code for issue in result.issues] == [FormIssueCode.UNKNOWN_FIELD]
    assert result.preview.title == "Laptop request"
    assert _field_names(result.preview.blocks) == AUTHOR_ORDER


async def test_validate_unknown_workflow_is_not_found(service: FormsService) -> None:
    with pytest.raises(TracecatNotFoundError):
        await service.validate_config(uuid.uuid4(), FormConfig())


async def test_render_resolves_an_online_form(
    session: AsyncSession, service: FormsService, published_workflow: Workflow
) -> None:
    form = await create_form(
        session,
        published_workflow,
        title="Laptop request",
        layout=[{"field": "a", "hidden": True}],
    )

    resolved = await service.render_form(form)

    assert resolved.id == form.id
    assert resolved.workspace_id == form.workspace_id
    assert resolved.status is FormStatus.ONLINE
    assert resolved.title == "Laptop request"
    assert resolved.definition_version == 1
    assert _field_names(resolved.blocks) == ["zeta", "mm"]
    zeta = resolved.blocks[0]
    assert isinstance(zeta, FormFieldBlock)
    assert zeta.help == "Why you need it"
    assert zeta.required


@pytest.mark.parametrize(
    ("status", "layout", "published", "code"),
    [
        (FormStatus.OFFLINE, None, True, FormErrorCode.FORM_OFFLINE),
        (FormStatus.ONLINE, None, False, FormErrorCode.FORM_UNAVAILABLE),
        (
            FormStatus.ONLINE,
            [{"field": "missing"}],
            True,
            FormErrorCode.FORM_UNAVAILABLE,
        ),
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

    with pytest.raises(FormError) as exc_info:
        await service.render_form(form)

    assert exc_info.value.code is code
    # Requesters are not told what is wrong with the config.
    assert exc_info.value.issues is None


# ----- Views and activity -----


async def _events(session: AsyncSession, form: Form) -> list[FormEvent]:
    result = await session.execute(
        select(FormEvent).where(FormEvent.form_id == form.id)
    )
    return list(result.scalars().all())


async def test_views_are_deduplicated_per_user(
    session: AsyncSession,
    service: FormsService,
    requester: User,
    published_workflow: Workflow,
) -> None:
    form = await create_form(session, published_workflow)

    await service.record_view(form)
    await service.record_view(form)

    [event] = await _events(session, form)
    assert event.type == FormEventType.FORM_VIEWED
    assert event.user_id == requester.id

    # Outside the window the same user counts again.
    await session.execute(
        update(FormEvent)
        .where(FormEvent.id == event.id)
        .values(created_at=datetime.now(UTC) - timedelta(minutes=6))
    )
    await session.commit()
    await service.record_view(form)

    assert len(await _events(session, form)) == 2
    assert (await service.read_form(form)).counts is not None


async def test_views_by_another_user_are_recorded(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    svc_workspace: Workspace,
    published_workflow: Workflow,
) -> None:
    form = await create_form(session, published_workflow)
    colleague = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    colleague_role = builder_role.model_copy(update={"user_id": colleague.id})

    await service.record_view(form)
    await FormsService(session, colleague_role).record_view(form)

    assert len(await _events(session, form)) == 2


async def test_service_account_views_are_not_recorded(
    session: AsyncSession, svc_workspace: Workspace, published_workflow: Workflow
) -> None:
    form = await create_form(session, published_workflow)
    api_key_role = Role(
        type="service_account",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        service_account_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )

    await FormsService(session, api_key_role).record_view(form)

    assert await _events(session, form) == []


async def test_recording_a_view_never_fails_the_request(
    session: AsyncSession,
    builder_role: Role,
    published_workflow: Workflow,
) -> None:
    form = await create_form(session, published_workflow)
    form_id = form.id
    # No user row behind this ID, so the insert violates a foreign key.
    ghost_role = builder_role.model_copy(update={"user_id": uuid.uuid4()})

    await FormsService(session, ghost_role).record_view(form)

    result = await session.execute(
        select(FormEvent.id).where(FormEvent.form_id == form_id)
    )
    assert result.first() is None


async def test_activity_lists_views_and_submissions_newest_first(
    session: AsyncSession,
    service: FormsService,
    requester: User,
    published_workflow: Workflow,
) -> None:
    form = await create_form(session, published_workflow)
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        submitted_by_user_id=requester.id,
        answers={},
        definition_version=1,
        wf_exec_id="wf_synthetic/exec_synthetic",
    )
    session.add(submission)
    await session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            FormEvent(
                workspace_id=form.workspace_id,
                form_id=form.id,
                type=FormEventType.FORM_VIEWED.value,
                user_id=requester.id,
                created_at=now - timedelta(minutes=2),
            ),
            FormEvent(
                workspace_id=form.workspace_id,
                form_id=form.id,
                type=FormEventType.FORM_SUBMITTED.value,
                user_id=requester.id,
                submission_id=submission.id,
                created_at=now - timedelta(minutes=1),
            ),
            # The actor was deleted since.
            FormEvent(
                workspace_id=form.workspace_id,
                form_id=form.id,
                type=FormEventType.FORM_VIEWED.value,
                created_at=now - timedelta(minutes=3),
            ),
        ]
    )
    await session.commit()

    page = await service.list_events(form, PageParams())

    assert [event.type for event in page.items] == [
        FormEventType.FORM_SUBMITTED,
        FormEventType.FORM_VIEWED,
        FormEventType.FORM_VIEWED,
    ]
    submitted, viewed, anonymous = page.items
    assert submitted.submission_id == submission.id
    assert submitted.reference == submission_reference(submission.id)
    assert submitted.actor is not None
    assert submitted.actor.type == "user"
    assert submitted.actor.email == "maya.okafor@example.com"
    assert viewed.reference is None
    assert anonymous.actor is None

    counts = (await service.read_form(form)).counts
    assert counts is not None
    assert (counts.views, counts.submissions) == (2, 1)


async def test_counts_need_submission_read(
    session: AsyncSession, intake_role: Role, published_workflow: Workflow
) -> None:
    form = await create_form(session, published_workflow)

    read = await FormsService(session, intake_role).read_form(form)

    assert read.counts is None


# ----- User search -----

USER_EXPECTS: dict[str, dict[str, Any]] = {
    "recipient": {"type": "dict"},
}
USER_LAYOUT = [{"field": "recipient", "widget": "user"}]


async def test_user_search_needs_a_user_field(
    session: AsyncSession, intake_role: Role, published_workflow: Workflow
) -> None:
    form = await create_form(session, published_workflow)

    with pytest.raises(TracecatNotFoundError):
        await FormsService(session, intake_role).search_users(form, PageParams())


async def test_form_editor_searches_users_before_saving_a_user_field(
    session: AsyncSession,
    service: FormsService,
    requester: User,
    published_workflow: Workflow,
) -> None:
    """An unsaved preview can add a user field the saved config lacks."""
    form = await create_form(session, published_workflow)

    found = await service.search_users(form, PageParams())

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
    arjun = await create_org_user(
        session,
        svc_workspace.organization_id,
        email="arjun.rao@example.com",
        first_name="Arjun",
        last_name="Rao",
    )
    await create_org_user(
        session,
        svc_workspace.organization_id,
        email="left.company@example.com",
        is_active=False,
    )
    other_workspace = await create_organization_with_workspace(session, name="other")
    await create_org_user(
        session, other_workspace.organization_id, email="outsider@example.com"
    )

    everyone = await service.search_users(form, PageParams())
    by_name = await service.search_users(form, PageParams(), search="rao")
    by_email = await service.search_users(form, PageParams(), search="MAYA.")
    literal = await service.search_users(form, PageParams(), search="%")

    assert [user.email for user in everyone.items] == [
        "arjun.rao@example.com",
        "maya.okafor@example.com",
    ]
    assert [user.user_id for user in by_name.items] == [arjun.id]
    assert by_name.items[0].first_name == "Arjun"
    assert [user.user_id for user in by_email.items] == [requester.id]
    # Wildcards in the search text are matched literally.
    assert literal.items == []


async def test_user_search_is_cursor_paginated(
    session: AsyncSession,
    service: FormsService,
    builder_role: Role,
    workflow: Workflow,
) -> None:
    await publish_workflow(session, builder_role, workflow, USER_EXPECTS)
    form = await create_form(session, workflow, layout=USER_LAYOUT)
    await create_org_user(
        session, builder_role.organization_id or uuid.uuid4(), email="a@example.com"
    )

    first = await service.search_users(form, PageParams(limit=1))
    assert first.next_cursor is not None
    second = await service.search_users(
        form, PageParams(limit=1, cursor=first.next_cursor)
    )

    assert [user.email for user in [*first.items, *second.items]] == [
        "a@example.com",
        "maya.okafor@example.com",
    ]
