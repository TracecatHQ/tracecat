"""Behaviour tests for the requester (/intake) routes.

These routes take a form ID with no workspace and have no ``require_scope``.
Access is decided per workspace inside the service, so it is tested here.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import (
    create_form,
    create_org_user,
    create_organization_with_workspace,
    create_workflow,
    use_session,
)
from tracecat.auth import credentials
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.db.models import (
    Form,
    FormSubmission,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.forms import requester_router
from tracecat.forms import service as forms_service
from tracecat.forms.enums import FormStatus
from tracecat.forms.service import FormsIntakeService, submission_reference
from tracecat.pagination import PageParams

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

type ScopesByWorkspace = dict[uuid.UUID, frozenset[str]]

INTAKE_SCOPES = frozenset({"form:read", "form:submit"})


@pytest.fixture
async def requester(session: AsyncSession, svc_workspace: Workspace) -> User:
    return await create_org_user(
        session, svc_workspace.organization_id, email="maya.okafor@example.com"
    )


@pytest.fixture
def role(svc_workspace: Workspace, requester: User) -> Role:
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
async def second_workspace(
    session: AsyncSession, svc_workspace: Workspace
) -> Workspace:
    """Another workspace of the caller's organization."""
    workspace = Workspace(
        name="second-workspace", organization_id=svc_workspace.organization_id
    )
    session.add(workspace)
    await session.commit()
    return workspace


@pytest.fixture
async def foreign_workspace(session: AsyncSession) -> Workspace:
    """A workspace of another organization."""
    return await create_organization_with_workspace(session, name="foreign")


@pytest.fixture
def scopes(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> ScopesByWorkspace:
    """Scopes the caller holds per workspace. Empty means none anywhere.

    Also points the RLS-bypass reads at the test session, which is the only
    connection that can see the test's rows.
    """
    scopes_by_workspace: ScopesByWorkspace = {}

    async def resolve(role: Role) -> frozenset[str]:
        assert role.workspace_id is not None
        return scopes_by_workspace.get(role.workspace_id, frozenset())

    resolver = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(forms_service, "compute_effective_scopes", resolver)
    monkeypatch.setattr(
        forms_service,
        "get_async_session_bypass_rls_context_manager",
        use_session(session),
    )
    return scopes_by_workspace


async def _form_in(
    session: AsyncSession,
    workspace: Workspace,
    *,
    title: str,
    status: FormStatus = FormStatus.ONLINE,
) -> Form:
    workflow = await create_workflow(session, workspace.id, title=f"{title} workflow")
    return await create_form(session, workflow, status=status, title=title)


async def _locate_status(session: AsyncSession, role: Role, form_id: uuid.UUID) -> int:
    try:
        await requester_router.locate_intake_form(
            role=role, session=session, form_id=form_id
        )
    except HTTPException as e:
        return e.status_code
    return 200


# ----- GET /intake/forms/{form_id} -----


async def test_locate_returns_the_workspace_of_a_readable_form(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    scopes[svc_workspace.id] = INTAKE_SCOPES

    location = await requester_router.locate_intake_form(
        role=role, session=session, form_id=form.id
    )

    assert location.model_dump() == {
        "form_id": form.id,
        "workspace_id": svc_workspace.id,
        "workspace_name": svc_workspace.name,
    }


async def test_locate_unknown_form_is_404(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    scopes[svc_workspace.id] = INTAKE_SCOPES

    assert await _locate_status(session, role, uuid.uuid4()) == 404


async def test_locate_form_without_form_read_is_404(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    # Other scopes in the workspace do not help.
    scopes[svc_workspace.id] = frozenset({"workflow:read", "form:submit"})

    assert await _locate_status(session, role, form.id) == 404


async def test_locate_form_in_another_workspace_is_404(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    second_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    form = await _form_in(session, second_workspace, title="Access request")
    # form:read in one workspace says nothing about another.
    scopes[svc_workspace.id] = INTAKE_SCOPES

    assert await _locate_status(session, role, form.id) == 404

    scopes[second_workspace.id] = INTAKE_SCOPES
    assert await _locate_status(session, role, form.id) == 200


async def test_locate_form_in_another_organization_is_404(
    session: AsyncSession,
    role: Role,
    foreign_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    form = await _form_in(session, foreign_workspace, title="Foreign form")
    # Even if scope resolution were to grant form:read there, the
    # organization check comes first and scopes are never consulted.
    scopes[foreign_workspace.id] = frozenset({"*"})

    assert await _locate_status(session, role, form.id) == 404
    resolver = forms_service.compute_effective_scopes
    assert isinstance(resolver, AsyncMock)
    resolver.assert_not_awaited()


async def test_locate_checks_scopes_for_the_forms_workspace(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    scopes[svc_workspace.id] = INTAKE_SCOPES

    await FormsIntakeService(session, role).locate_form(form.id)

    resolver = forms_service.compute_effective_scopes
    assert isinstance(resolver, AsyncMock)
    [call] = resolver.await_args_list
    checked_role = call.args[0]
    assert checked_role.workspace_id == svc_workspace.id
    assert checked_role.user_id == role.user_id
    assert checked_role.organization_id == role.organization_id


async def test_locate_offline_form_resolves_for_a_reader(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    """Offline is not an access failure: the render route answers 409 for it."""
    form = await _form_in(
        session, svc_workspace, title="Offboarding", status=FormStatus.OFFLINE
    )

    assert await _locate_status(session, role, form.id) == 404

    scopes[svc_workspace.id] = INTAKE_SCOPES
    assert await _locate_status(session, role, form.id) == 200


async def test_locate_rejects_non_user_roles(
    session: AsyncSession, svc_workspace: Workspace, scopes: ScopesByWorkspace
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    scopes[svc_workspace.id] = INTAKE_SCOPES
    api_key_role = Role(
        type="service_account",
        organization_id=svc_workspace.organization_id,
        service_account_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )

    assert await _locate_status(session, api_key_role, form.id) == 404


# ----- GET /intake/forms -----


async def test_catalog_lists_online_forms_from_readable_workspaces(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    second_workspace: Workspace,
    foreign_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    visible = await _form_in(session, svc_workspace, title="Laptop request")
    await _form_in(
        session, svc_workspace, title="Offboarding", status=FormStatus.OFFLINE
    )
    await _form_in(session, second_workspace, title="Access request")
    await _form_in(session, foreign_workspace, title="Foreign form")
    scopes[svc_workspace.id] = INTAKE_SCOPES
    scopes[second_workspace.id] = frozenset({"workflow:read"})
    scopes[foreign_workspace.id] = frozenset({"*"})

    page = await requester_router.list_intake_forms(
        role=role, session=session, page=PageParams()
    )

    assert [form.model_dump() for form in page.items] == [
        {
            "id": visible.id,
            "workspace_id": svc_workspace.id,
            "workspace_name": svc_workspace.name,
            "title": "Laptop request",
            "description": "Request a new laptop from IT.",
        }
    ]


async def test_catalog_spans_every_readable_workspace(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    second_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    first = await _form_in(session, svc_workspace, title="Laptop request")
    second = await _form_in(session, second_workspace, title="Access request")
    scopes[svc_workspace.id] = INTAKE_SCOPES
    scopes[second_workspace.id] = INTAKE_SCOPES

    page_one = await FormsIntakeService(session, role).list_forms(PageParams(limit=1))
    assert page_one.next_cursor is not None
    page_two = await FormsIntakeService(session, role).list_forms(
        PageParams(limit=1, cursor=page_one.next_cursor)
    )

    assert {form.id for form in [*page_one.items, *page_two.items]} == {
        first.id,
        second.id,
    }
    assert {form.workspace_name for form in [*page_one.items, *page_two.items]} == {
        svc_workspace.name,
        second_workspace.name,
    }


async def test_catalog_is_empty_without_form_read_anywhere(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    await _form_in(session, svc_workspace, title="Laptop request")

    page = await FormsIntakeService(session, role).list_forms(PageParams())

    assert page.items == []


# ----- GET /intake/submissions -----


async def _submit_as(
    session: AsyncSession, form: Form, user_id: uuid.UUID | None
) -> FormSubmission:
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        submitted_by_user_id=user_id,
        answers={"justification": "Synthetic answer"},
        definition_version=1,
        wf_exec_id="wf_synthetic/exec_synthetic",
    )
    session.add(submission)
    await session.commit()
    return submission


async def test_my_submissions_lists_only_the_callers_own(
    session: AsyncSession,
    role: Role,
    requester: User,
    svc_workspace: Workspace,
    second_workspace: Workspace,
    foreign_workspace: Workspace,
    scopes: ScopesByWorkspace,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    hidden_form = await _form_in(session, second_workspace, title="Access request")
    foreign_form = await _form_in(session, foreign_workspace, title="Foreign form")
    colleague = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    mine = await _submit_as(session, form, requester.id)
    await _submit_as(session, form, colleague.id)
    await _submit_as(session, form, None)
    # The caller's own, but in places they can no longer read forms.
    await _submit_as(session, hidden_form, requester.id)
    await _submit_as(session, foreign_form, requester.id)
    scopes[svc_workspace.id] = INTAKE_SCOPES
    scopes[foreign_workspace.id] = frozenset({"*"})

    page = await requester_router.list_intake_submissions(
        role=role, session=session, page=PageParams()
    )

    [item] = page.items
    assert item.id == mine.id
    assert item.reference == submission_reference(mine.id)
    assert item.form_id == form.id
    assert item.form_title == "Laptop request"
    assert item.workspace_name == svc_workspace.name
    # Requesters never get run fields or answers from this route.
    assert set(item.model_dump()) == {
        "id",
        "reference",
        "form_id",
        "form_title",
        "workspace_id",
        "workspace_name",
        "submitted_at",
    }


# ----- Real scope resolution -----
#
# The tests above hand the service a scope set per workspace. These resolve
# scopes the way a request does: ``compute_effective_scopes`` runs the RBAC
# query over role assignments stored in the database.


@pytest.fixture
def rbac(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Run the real scope query, and the RLS-bypass reads, on the test session.

    The test's rows are uncommitted, so only its own connection can see them.
    """
    monkeypatch.setattr(
        credentials, "get_async_session_auth_context_manager", use_session(session)
    )
    monkeypatch.setattr(
        forms_service,
        "get_async_session_bypass_rls_context_manager",
        use_session(session),
    )
    _clear_scope_cache()


def _clear_scope_cache() -> None:
    credentials._compute_effective_scopes_cached.cache_clear()


async def _assign_role(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    scope_names: frozenset[str],
) -> UserRoleAssignment:
    """Give a user a custom role holding these scopes in one workspace."""
    role = DBRole(
        name=f"Synthetic role {uuid.uuid4().hex[:8]}",
        organization_id=workspace.organization_id,
    )
    session.add(role)
    await session.flush()
    for name in sorted(scope_names):
        resource, _, action = name.rpartition(":")
        scope = await session.scalar(
            select(Scope).where(
                Scope.organization_id == workspace.organization_id, Scope.name == name
            )
        )
        if scope is None:
            scope = Scope(
                name=name,
                resource=resource,
                action=action,
                source=ScopeSource.CUSTOM,
                organization_id=workspace.organization_id,
            )
            session.add(scope)
            await session.flush()
        session.add(RoleScope(role_id=role.id, scope_id=scope.id))
    assignment = UserRoleAssignment(
        organization_id=workspace.organization_id,
        user_id=user.id,
        workspace_id=workspace.id,
        role_id=role.id,
    )
    session.add(assignment)
    await session.commit()
    _clear_scope_cache()
    return assignment


@pytest.mark.usefixtures("rbac")
async def test_locate_follows_the_callers_role_assignment(
    session: AsyncSession,
    role: Role,
    requester: User,
    svc_workspace: Workspace,
    second_workspace: Workspace,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    elsewhere = await _form_in(session, second_workspace, title="Access request")

    # An organization member with no role in the workspace reads no forms.
    assert await _locate_status(session, role, form.id) == 404

    assignment = await _assign_role(session, requester, svc_workspace, INTAKE_SCOPES)
    assert await _locate_status(session, role, form.id) == 200
    # The assignment is for one workspace.
    assert await _locate_status(session, role, elsewhere.id) == 404

    await session.delete(assignment)
    await session.commit()
    _clear_scope_cache()
    assert await _locate_status(session, role, form.id) == 404


@pytest.mark.usefixtures("rbac")
@pytest.mark.parametrize(
    ("scope_names", "status"),
    [
        (frozenset({"form:read"}), 200),
        (frozenset({"form:*"}), 200),
        (frozenset({"form:submit", "workflow:read"}), 404),
        (frozenset({"form:submission:read"}), 404),
    ],
    ids=["form_read", "wildcard", "other_scopes", "submission_read_only"],
)
async def test_locate_needs_form_read_from_the_assigned_role(
    session: AsyncSession,
    role: Role,
    requester: User,
    svc_workspace: Workspace,
    scope_names: frozenset[str],
    status: int,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    await _assign_role(session, requester, svc_workspace, scope_names)

    assert await _locate_status(session, role, form.id) == status


@pytest.mark.usefixtures("rbac")
async def test_another_users_role_assignment_grants_the_caller_nothing(
    session: AsyncSession,
    role: Role,
    svc_workspace: Workspace,
) -> None:
    form = await _form_in(session, svc_workspace, title="Laptop request")
    colleague = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    await _assign_role(session, colleague, svc_workspace, INTAKE_SCOPES)

    assert await _locate_status(session, role, form.id) == 404


@pytest.mark.usefixtures("rbac")
async def test_catalog_and_submissions_follow_role_assignments(
    session: AsyncSession,
    role: Role,
    requester: User,
    svc_workspace: Workspace,
    second_workspace: Workspace,
) -> None:
    visible = await _form_in(session, svc_workspace, title="Laptop request")
    hidden = await _form_in(session, second_workspace, title="Access request")
    mine = await _submit_as(session, visible, requester.id)
    await _submit_as(session, hidden, requester.id)
    await _assign_role(session, requester, svc_workspace, INTAKE_SCOPES)
    await _assign_role(
        session, requester, second_workspace, frozenset({"workflow:read"})
    )

    forms = await requester_router.list_intake_forms(
        role=role, session=session, page=PageParams()
    )
    submissions = await requester_router.list_intake_submissions(
        role=role, session=session, page=PageParams()
    )

    assert [form.id for form in forms.items] == [visible.id]
    assert [submission.id for submission in submissions.items] == [mine.id]
