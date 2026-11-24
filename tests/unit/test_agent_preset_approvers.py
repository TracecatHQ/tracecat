"""Who may decide an agent preset's tool approvals, and how the set is stored."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.agent.activities import (
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat_ee.agent.approvals.service import ApprovalManager
from tracecat_ee.agent.context import AgentContext

from tests.support.membership import grant_workspace_membership
from tests.unit.test_agent_channel_users import _identity, _link, _Org
from tests.unit.test_agent_session_approval_sink import (
    _ApprovalContinuationRedis,
    _mock_approval_continuation_dependencies,
)
from tests.unit.test_agent_session_decide import (
    TOOL_CALLS,
    _approval_service_on,
    _click,
    _row,
    _seed_batch,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.approvals.policy import (
    ApproverNotAllowedError,
    ensure_approver_allowed,
    ensure_decider_allowed,
)
from tracecat.agent.channel_users.service import resolve_channel_actor
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.preset.approvers import AgentPresetApproverService
from tracecat.agent.preset.schemas import (
    MAX_AGENT_PRESET_APPROVERS,
    AgentPresetApproverRead,
    AgentPresetApproverRef,
    AgentPresetApproversUpdate,
)
from tracecat.agent.session.results import DecisionRecorded, DecisionRejected
from tracecat.agent.session.schemas import AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.types import Role
from tracecat.authz.membership import ensure_member
from tracecat.authz.scopes import EDITOR_SCOPES, VIEWER_SCOPES
from tracecat.authz.seeding import seed_system_roles_for_org, seed_system_scopes
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest
from tracecat.db.models import (
    AgentPreset,
    AgentPresetApprover,
    AgentSession,
    Approval,
    Group,
    GroupMember,
    GroupRoleAssignment,
    Organization,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatValidationError,
)
from tracecat.pagination import PageParams
from tracecat.tiers import defaults as tier_defaults

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


def _set_rbac_addons(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"rbac_addons": enabled}),
    )


@pytest.fixture(autouse=True)
def enable_rbac_addons(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_rbac_addons(monkeypatch, True)


async def _make_user(
    session: AsyncSession, role: Role, *, workspace_role: str | None
) -> User:
    """A user in the role's org, holding ``workspace_role`` in its workspace."""
    assert role.organization_id is not None and role.workspace_id is not None
    user = User(
        id=uuid.uuid4(),
        email=f"approver-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()
    await seed_system_scopes(session)
    await seed_system_roles_for_org(session, role.organization_id)
    await ensure_member(session, role.organization_id, user.id)
    if workspace_role is not None:
        await grant_workspace_membership(
            session,
            user_id=user.id,
            organization_id=role.organization_id,
            workspace_id=role.workspace_id,
            slug=workspace_role,
        )
    await session.commit()
    return user


async def _system_role_id(session: AsyncSession, role: Role, slug: str) -> uuid.UUID:
    role_id = await session.scalar(
        select(DBRole.id).where(
            DBRole.organization_id == role.organization_id, DBRole.slug == slug
        )
    )
    assert role_id is not None
    return role_id


async def _preset(session: AsyncSession, role: Role) -> AgentPreset:
    assert role.workspace_id is not None
    preset = AgentPreset(
        workspace_id=role.workspace_id,
        name="Responder",
        slug=f"responder-{uuid.uuid4().hex[:8]}",
        model_name="gpt-5",
        model_provider="openai",
    )
    session.add(preset)
    await session.commit()
    return preset


async def _list(
    session: AsyncSession,
    role: Role,
    preset_id: uuid.UUID,
    **principal: uuid.UUID,
) -> None:
    assert role.workspace_id is not None
    session.add(
        AgentPresetApprover(
            workspace_id=role.workspace_id, preset_id=preset_id, **principal
        )
    )
    await session.commit()


async def _allowed(
    session: AsyncSession, role: Role, preset: AgentPreset, user: User | None
) -> bool:
    assert role.organization_id is not None and role.workspace_id is not None
    try:
        await ensure_approver_allowed(
            session,
            organization_id=role.organization_id,
            workspace_id=role.workspace_id,
            preset_id=preset.id,
            approved_by=user.id if user else None,
        )
    except ApproverNotAllowedError:
        return False
    return True


async def test_empty_policy_keeps_the_legacy_rule(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    editor = await _make_user(session, svc_role, workspace_role="workspace-editor")
    viewer = await _make_user(session, svc_role, workspace_role="workspace-viewer")

    assert await _allowed(session, svc_role, preset, None)
    assert await _allowed(session, svc_role, preset, editor)
    # Route scopes gate the legacy rule, so a linked channel clicker is not narrowed.
    assert await _allowed(session, svc_role, preset, viewer)


async def test_listed_user_group_and_role_may_decide(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.organization_id is not None
    preset = await _preset(session, svc_role)
    listed = await _make_user(session, svc_role, workspace_role="workspace-editor")
    in_group = await _make_user(session, svc_role, workspace_role="workspace-editor")
    by_role = await _make_user(session, svc_role, workspace_role="workspace-editor")
    outsider = await _make_user(session, svc_role, workspace_role="workspace-editor")
    group = Group(
        name=f"oncall-{uuid.uuid4().hex[:8]}", organization_id=svc_role.organization_id
    )
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=in_group.id))
    custom = DBRole(
        name=f"approver-{uuid.uuid4().hex[:8]}",
        organization_id=svc_role.organization_id,
    )
    session.add(custom)
    await session.flush()
    session.add(
        UserRoleAssignment(
            organization_id=svc_role.organization_id,
            user_id=by_role.id,
            workspace_id=None,
            role_id=custom.id,
        )
    )
    await session.commit()

    await _list(session, svc_role, preset.id, user_id=listed.id)
    await _list(session, svc_role, preset.id, group_id=group.id)
    await _list(session, svc_role, preset.id, role_id=custom.id)

    assert await _allowed(session, svc_role, preset, listed)
    assert await _allowed(session, svc_role, preset, in_group)
    assert await _allowed(session, svc_role, preset, by_role)
    assert not await _allowed(session, svc_role, preset, outsider)
    assert not await _allowed(session, svc_role, preset, None)


async def test_listed_approver_still_needs_agent_execute(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    viewer = await _make_user(session, svc_role, workspace_role="workspace-viewer")
    await _list(session, svc_role, preset.id, user_id=viewer.id)

    assert not await _allowed(session, svc_role, preset, viewer)


async def test_workspace_admin_is_always_allowed(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    admin = await _make_user(session, svc_role, workspace_role="workspace-admin")
    listed = await _make_user(session, svc_role, workspace_role="workspace-editor")
    await _list(session, svc_role, preset.id, user_id=listed.id)

    assert await _allowed(session, svc_role, preset, admin)
    # The last listed approver leaving never strands a pending approval.
    await session.execute(delete(User).where(cast(Any, User.id) == listed.id))
    await session.commit()
    assert await _allowed(session, svc_role, preset, admin)


async def test_batch_spanning_presets_needs_every_presets_approval(
    session: AsyncSession, svc_role: Role
) -> None:
    organization_id, workspace_id = svc_role.organization_id, svc_role.workspace_id
    assert organization_id is not None and workspace_id is not None
    first = await _preset(session, svc_role)
    second = await _preset(session, svc_role)
    both = await _make_user(session, svc_role, workspace_role="workspace-editor")
    only_first = await _make_user(session, svc_role, workspace_role="workspace-editor")
    agent_session = cast(AgentSession, SimpleNamespace(agent_preset_id=None))
    approvals = [
        cast(Approval, SimpleNamespace(agent_preset_id=first.id)),
        cast(Approval, SimpleNamespace(agent_preset_id=second.id)),
    ]

    async def allowed(user: User) -> bool:
        try:
            await ensure_decider_allowed(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_session=agent_session,
                approvals=approvals,
                approved_by=user.id,
            )
        except ApproverNotAllowedError:
            return False
        return True

    assert await allowed(only_first)
    await _list(session, svc_role, first.id, user_id=both.id)
    await _list(session, svc_role, first.id, user_id=only_first.id)
    await _list(session, svc_role, second.id, user_id=both.id)

    assert await allowed(both)
    assert not await allowed(only_first)


async def test_decide_refuses_outside_the_policy_and_leaves_rows_pending(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    listed = await _make_user(session, svc_role, workspace_role="workspace-editor")
    outsider = await _make_user(session, svc_role, workspace_role="workspace-editor")
    await _list(session, svc_role, preset.id, user_id=listed.id)
    agent_session = await _seed_batch(session, svc_role)
    agent_session.agent_preset_id = preset.id
    await session.commit()
    service = AgentSessionService(session=session, role=svc_role)
    execute_update = AsyncMock(return_value=False)

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        refused = [
            await service.decide(**_click(agent_session, user_id=user_id))
            for user_id in (outsider.id, None)
        ]
        execute_update.assert_not_awaited()
        for tool_call_id in TOOL_CALLS:
            row = await _row(session, agent_session, tool_call_id)
            assert row.status is ApprovalStatus.PENDING
        accepted = await service.decide(**_click(agent_session, user_id=listed.id))

    not_allowed = DecisionRejected(reason="not_allowed", tool_call_id="tool_call_123")
    assert refused == [not_allowed, not_allowed]
    assert accepted == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    row = await _row(session, agent_session, "tool_call_123")
    assert row.approved_by == listed.id


async def test_slack_clicker_decides_as_their_linked_tracecat_user(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.organization_id is not None
    preset = await _preset(session, svc_role)
    assert svc_role.workspace_id is not None
    linked = await _make_user(session, svc_role, workspace_role="workspace-editor")
    unlinked = await _make_user(session, svc_role, workspace_role="workspace-editor")
    org = _Org(svc_role.organization_id, svc_role.workspace_id)
    await _link(session, org, "U_LINKED", linked)
    for user in (linked, unlinked):
        await _list(session, svc_role, preset.id, user_id=user.id)
    agent_session = await _seed_batch(session, svc_role)
    agent_session.agent_preset_id = preset.id
    await session.commit()
    linked_actor, unlinked_actor = [
        await resolve_channel_actor(
            session,
            organization_id=svc_role.organization_id,
            identity=_identity(slack_user),
        )
        for slack_user in ("U_LINKED", "U_UNLINKED")
    ]
    service = AgentSessionService(session=session, role=svc_role)

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), AsyncMock(return_value=False)
    ):
        refused = await service.decide(
            **_click(agent_session, user_id=unlinked_actor.user_id, source="slack")
        )
        accepted = await service.decide(
            **_click(agent_session, user_id=linked_actor.user_id, source="slack")
        )

    assert unlinked_actor.user_id is None
    assert refused == DecisionRejected(
        reason="not_allowed", tool_call_id="tool_call_123"
    )
    assert accepted == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    row = await _row(session, agent_session, "tool_call_123")
    assert row.approved_by == linked.id


async def test_switching_presets_after_the_pause_keeps_the_runs_policy(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    listed = await _make_user(session, svc_role, workspace_role="workspace-editor")
    outsider = await _make_user(session, svc_role, workspace_role="workspace-editor")
    await _list(session, svc_role, preset.id, user_id=listed.id)
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Chat",
        workspace_id=svc_role.workspace_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        agent_preset_id=preset.id,
        curr_run_id=uuid.uuid4(),
        active_stream_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.commit()
    service = AgentSessionService(session=session, role=svc_role)
    execute_update = AsyncMock(return_value=False)

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), execute_update
        ),
        _approval_service_on(session, svc_role),
    ):
        # The run pauses on the preset it was configured from.
        await ApprovalManager.record_approval_requests(
            PersistApprovalsActivityInputs(
                role=svc_role,
                session_id=agent_session.id,
                approvals=[
                    ToolApprovalPayload(
                        tool_call_id=tool_call_id, tool_name="core.http_request"
                    )
                    for tool_call_id in TOOL_CALLS
                ],
                agent_preset_id=preset.id,
            )
        )
        await service.update_session(
            agent_session, params=AgentSessionUpdate(agent_preset_id=None)
        )
        assert agent_session.agent_preset_id is None
        refused = await service.decide(**_click(agent_session, user_id=outsider.id))
        accepted = await service.decide(**_click(agent_session, user_id=listed.id))

    assert refused == DecisionRejected(
        reason="not_allowed", tool_call_id="tool_call_123"
    )
    assert accepted == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    row = await _row(session, agent_session, "tool_call_123")
    assert row.agent_preset_id == preset.id
    assert row.approved_by == listed.id


async def test_an_items_own_preset_overrides_the_batch_preset(
    session: AsyncSession, svc_role: Role
) -> None:
    root = await _preset(session, svc_role)
    child = await _preset(session, svc_role)
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Chat",
        workspace_id=svc_role.workspace_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        agent_preset_id=root.id,
    )
    session.add(agent_session)
    await session.commit()

    async def execute_activity(activity: Any, arg: Any, **_: Any) -> Any:
        return await activity(arg)

    AgentContext.set(session_id=agent_session.id)
    with (
        _approval_service_on(session, svc_role),
        patch("temporalio.workflow.execute_activity", execute_activity),
    ):
        # A subagent's call rides in its root's batch with its own preset.
        await ApprovalManager(role=svc_role).prepare(
            [
                ToolCallContent(id="root_call", name="core.http_request"),
                ToolCallContent(
                    id="child_call", name="core.http_request", agent_preset_id=child.id
                ),
            ],
            agent_preset_id=root.id,
        )

    root_row = await _row(session, agent_session, "root_call")
    child_row = await _row(session, agent_session, "child_call")
    assert root_row.agent_preset_id == root.id
    assert child_row.agent_preset_id == child.id


async def test_web_continuation_outside_the_policy_is_refused(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    listed = await _make_user(session, svc_role, workspace_role="workspace-editor")
    await _list(session, svc_role, preset.id, user_id=listed.id)
    agent_session = await _seed_batch(session, svc_role)
    agent_session.agent_preset_id = preset.id
    await session.commit()
    # svc_role's user has no account row, so it decides as no one.
    service = AgentSessionService(session=session, role=svc_role)
    execute_update = AsyncMock(return_value=False)

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), execute_update
        ),
        pytest.raises(ApproverNotAllowedError),
    ):
        await service.run_turn(
            agent_session.id,
            ContinueRunRequest(
                decisions=[
                    ApprovalDecision(tool_call_id=tool_call_id, action="approve")
                    for tool_call_id in TOOL_CALLS
                ]
            ),
        )

    execute_update.assert_not_awaited()
    for tool_call_id in TOOL_CALLS:
        row = await _row(session, agent_session, tool_call_id)
        assert row.status is ApprovalStatus.PENDING


async def _assigned_group(
    session: AsyncSession,
    role: Role,
    role_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID | None,
) -> Group:
    """An org group granted ``role_id`` in ``workspace_id`` (None = org-wide)."""
    assert role.organization_id is not None
    group = Group(
        name=f"oncall-{uuid.uuid4().hex[:8]}", organization_id=role.organization_id
    )
    session.add(group)
    await session.flush()
    session.add(
        GroupRoleAssignment(
            organization_id=role.organization_id,
            group_id=group.id,
            workspace_id=workspace_id,
            role_id=role_id,
        )
    )
    await session.commit()
    return group


async def _custom_role(session: AsyncSession, role: Role) -> DBRole:
    assert role.organization_id is not None
    custom = DBRole(
        name=f"Triage {uuid.uuid4().hex[:8]}",
        slug=f"triage-{uuid.uuid4().hex[:8]}",
        organization_id=role.organization_id,
    )
    session.add(custom)
    await session.commit()
    return custom


async def _all_listed(
    service: AgentPresetApproverService, preset_id: uuid.UUID
) -> list[AgentPresetApproverRead]:
    seen: list[AgentPresetApproverRead] = []
    cursor: str | None = None
    while True:
        page = await service.list_approvers(
            preset_id, PageParams(limit=1, cursor=cursor)
        )
        seen.extend(page.items)
        if not page.has_more:
            return seen
        cursor = page.next_cursor


async def _option_keys(
    service: AgentPresetApproverService,
) -> set[tuple[str, uuid.UUID]]:
    options = await service.list_options(PageParams(limit=100))
    return {(o.kind, o.id) for o in options.items}


async def test_add_and_remove_approvers_one_at_a_time(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    preset = await _preset(session, svc_role)
    member = await _make_user(session, svc_role, workspace_role="workspace-editor")
    editor_role_id = await _system_role_id(session, svc_role, "workspace-editor")
    group = await _assigned_group(
        session, svc_role, editor_role_id, workspace_id=svc_role.workspace_id
    )
    service = AgentPresetApproverService(session, role=svc_role)
    user_ref = AgentPresetApproverRef(kind="user", id=member.id)
    group_ref = AgentPresetApproverRef(kind="group", id=group.id)
    role_ref = AgentPresetApproverRef(kind="role", id=editor_role_id)

    added = await service.add_approvers(preset.id, [role_ref, user_ref, user_ref])
    assert [(r.kind, r.id) for r in added] == [
        ("user", member.id),
        ("role", editor_role_id),
    ]
    assert added[0].email == member.email
    # Adding again is a no-op, not a conflict.
    await service.add_approvers(preset.id, [user_ref, group_ref])
    listed = await _all_listed(service, preset.id)
    assert [(r.kind, r.id) for r in listed] == [
        ("user", member.id),
        ("group", group.id),
        ("role", editor_role_id),
    ]
    assert {(r.kind, r.id) for r in listed} <= await _option_keys(service)

    await service.remove_approvers(preset.id, [group_ref, group_ref])
    await service.remove_approvers(preset.id, [group_ref])
    assert [r.id for r in await _all_listed(service, preset.id)] == [
        member.id,
        editor_role_id,
    ]
    await service.remove_approvers(preset.id, [user_ref, role_ref])
    assert await _all_listed(service, preset.id) == []


async def test_approver_options_page_in_order_and_search(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    member = await _make_user(session, svc_role, workspace_role="workspace-editor")
    viewer_role_id = await _system_role_id(session, svc_role, "workspace-viewer")
    group = await _assigned_group(
        session, svc_role, viewer_role_id, workspace_id=svc_role.workspace_id
    )
    service = AgentPresetApproverService(session, role=svc_role)

    seen: list[AgentPresetApproverRead] = []
    cursor: str | None = None
    while True:
        page = await service.list_options(PageParams(limit=1, cursor=cursor))
        seen.extend(page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    kinds = [option.kind for option in seen]
    assert kinds == sorted(kinds, key=["user", "group", "role"].index)
    assert len({(o.kind, o.id) for o in seen}) == len(seen)
    assert ("user", member.id) in {(o.kind, o.id) for o in seen}

    found = await service.list_options(PageParams(limit=100), search=group.name[-8:])
    assert [(o.kind, o.id) for o in found.items] == [("group", group.id)]
    assert (
        await service.list_options(PageParams(limit=100), search=member.email.upper())
    ).items[0].id == member.id


async def test_options_hold_only_principals_that_apply_in_this_workspace(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.organization_id is not None and svc_role.workspace_id is not None
    preset = await _preset(session, svc_role)
    await seed_system_scopes(session)
    await seed_system_roles_for_org(session, svc_role.organization_id)
    other = Workspace(name="Other", organization_id=svc_role.organization_id)
    session.add(other)
    await session.flush()
    assigned_here = await _custom_role(session, svc_role)
    assigned_elsewhere = await _custom_role(session, svc_role)
    unassigned = await _custom_role(session, svc_role)
    here = await _assigned_group(
        session, svc_role, assigned_here.id, workspace_id=svc_role.workspace_id
    )
    elsewhere = await _assigned_group(
        session, svc_role, assigned_elsewhere.id, workspace_id=other.id
    )
    org_only = Group(
        name=f"org-only-{uuid.uuid4().hex[:8]}",
        organization_id=svc_role.organization_id,
    )
    session.add(org_only)
    await session.commit()
    viewer_role_id = await _system_role_id(session, svc_role, "workspace-viewer")
    org_admin_id = await _system_role_id(session, svc_role, "organization-admin")
    # Org-wide grants apply here, but only workspace grants make an option.
    org_wide = await _assigned_group(
        session, svc_role, viewer_role_id, workspace_id=None
    )
    await _assigned_group(session, svc_role, org_admin_id, workspace_id=None)
    org_wide_custom = await _custom_role(session, svc_role)
    await _assigned_group(session, svc_role, org_wide_custom.id, workspace_id=None)
    # Organization roles are never offered, even when assigned in this workspace,
    # nor is a group whose only grant here is one.
    org_role_group = await _assigned_group(
        session, svc_role, org_admin_id, workspace_id=svc_role.workspace_id
    )
    service = AgentPresetApproverService(session, role=svc_role)
    preset_id = preset.id

    inside = [
        AgentPresetApproverRef(kind="role", id=assigned_here.id),
        AgentPresetApproverRef(kind="group", id=here.id),
    ]
    options = await _option_keys(service)
    assert {(ref.kind, ref.id) for ref in inside} <= options
    for system_slug in ("workspace-admin", "workspace-editor", "workspace-viewer"):
        role_id = await _system_role_id(session, svc_role, system_slug)
        assert ("role", role_id) in options
    outside = [
        AgentPresetApproverRef(kind="group", id=elsewhere.id),
        AgentPresetApproverRef(kind="group", id=org_only.id),
        AgentPresetApproverRef(kind="role", id=assigned_elsewhere.id),
        AgentPresetApproverRef(kind="role", id=unassigned.id),
        AgentPresetApproverRef(kind="group", id=org_wide.id),
        AgentPresetApproverRef(kind="role", id=org_admin_id),
        AgentPresetApproverRef(kind="role", id=org_wide_custom.id),
        AgentPresetApproverRef(kind="group", id=org_role_group.id),
    ]
    assert not {(ref.kind, ref.id) for ref in outside} & options

    for ref in outside:
        with pytest.raises(TracecatValidationError):
            await service.add_approvers(preset_id, [ref])
        await session.rollback()
    added = await service.add_approvers(preset_id, inside)
    assert {r.id for r in added} == {ref.id for ref in inside}


async def test_a_user_and_their_group_are_two_grants(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    preset = await _preset(session, svc_role)
    member = await _make_user(session, svc_role, workspace_role="workspace-editor")
    editor_role_id = await _system_role_id(session, svc_role, "workspace-editor")
    group = await _assigned_group(
        session, svc_role, editor_role_id, workspace_id=svc_role.workspace_id
    )
    session.add(GroupMember(group_id=group.id, user_id=member.id))
    await session.commit()
    service = AgentPresetApproverService(session, role=svc_role)

    added = await service.add_approvers(
        preset.id,
        [
            AgentPresetApproverRef(kind="user", id=member.id),
            AgentPresetApproverRef(kind="group", id=group.id),
        ],
    )

    assert [(r.kind, r.id) for r in added] == [
        ("user", member.id),
        ("group", group.id),
    ]
    rows = (
        await session.scalars(
            select(AgentPresetApprover.id).where(
                AgentPresetApprover.preset_id == preset.id
            )
        )
    ).all()
    assert len(rows) == 2
    assert await _allowed(session, svc_role, preset, member)


async def test_add_refuses_principals_outside_the_workspace(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    other_org = Organization(
        id=uuid.uuid4(), name="Other", slug=f"other-{uuid.uuid4().hex[:8]}"
    )
    session.add(other_org)
    await session.flush()
    foreign_group = Group(name="foreign", organization_id=other_org.id)
    session.add(foreign_group)
    # In the org, but holds no role path into this workspace.
    stranger = await _make_user(session, svc_role, workspace_role=None)
    member = await _make_user(session, svc_role, workspace_role="workspace-editor")
    service = AgentPresetApproverService(session, role=svc_role)
    preset_id = preset.id
    await session.commit()
    refs = [
        AgentPresetApproverRef(kind="group", id=foreign_group.id),
        AgentPresetApproverRef(kind="user", id=stranger.id),
        AgentPresetApproverRef(kind="role", id=uuid.uuid4()),
        # A real member named as a group must not slip through.
        AgentPresetApproverRef(kind="group", id=member.id),
    ]

    for ref in refs:
        with pytest.raises(TracecatValidationError):
            await service.add_approvers(preset_id, [ref])
        await session.rollback()

    assert await _all_listed(service, preset_id) == []


async def test_add_refuses_going_over_the_cap(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tracecat.agent.preset.approvers.MAX_AGENT_PRESET_APPROVERS", 2)
    preset = await _preset(session, svc_role)
    users = [
        await _make_user(session, svc_role, workspace_role="workspace-editor")
        for _ in range(3)
    ]
    service = AgentPresetApproverService(session, role=svc_role)
    preset_id = preset.id
    refs = [AgentPresetApproverRef(kind="user", id=user.id) for user in users]

    await service.add_approvers(preset_id, refs[:2])
    # Re-adding listed approvers does not count against the cap.
    await service.add_approvers(preset_id, refs[:2])
    with pytest.raises(TracecatValidationError, match="at most 2"):
        await service.add_approvers(preset_id, refs[2:])
    await session.rollback()

    assert {r.id for r in await _all_listed(service, preset_id)} == {
        users[0].id,
        users[1].id,
    }


async def test_only_agent_editors_may_see_options_or_change_approvers(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    viewer = AgentPresetApproverService(
        session, role=svc_role.model_copy(update={"scopes": VIEWER_SCOPES})
    )
    editor = AgentPresetApproverService(
        session, role=svc_role.model_copy(update={"scopes": EDITOR_SCOPES})
    )

    with pytest.raises(ScopeDeniedError):
        await viewer.list_options(PageParams(limit=10))
    with pytest.raises(ScopeDeniedError):
        await viewer.add_approvers(preset.id, [])
    with pytest.raises(ScopeDeniedError):
        await viewer.remove_approvers(preset.id, [])
    assert (await viewer.list_approvers(preset.id, PageParams(limit=10))).items == []
    await editor.list_options(PageParams(limit=10))
    assert await editor.add_approvers(preset.id, []) == []
    await editor.remove_approvers(preset.id, [])


async def test_a_listed_user_who_left_the_workspace_stays_listed(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    preset = await _preset(session, svc_role)
    leaver = await _make_user(session, svc_role, workspace_role="workspace-editor")
    joiner = await _make_user(session, svc_role, workspace_role="workspace-editor")
    await _list(session, svc_role, preset.id, user_id=leaver.id)
    await session.execute(
        delete(UserRoleAssignment).where(UserRoleAssignment.user_id == leaver.id)
    )
    await session.commit()
    service = AgentPresetApproverService(session, role=svc_role)

    added = await service.add_approvers(
        preset.id,
        [
            AgentPresetApproverRef(kind="user", id=leaver.id),
            AgentPresetApproverRef(kind="user", id=joiner.id),
        ],
    )

    assert {r.id for r in added} == {leaver.id, joiner.id}


async def test_deleting_the_last_listed_principal_empties_the_policy(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.organization_id is not None
    preset = await _preset(session, svc_role)
    editor = await _make_user(session, svc_role, workspace_role="workspace-editor")
    group = Group(
        name=f"oncall-{uuid.uuid4().hex[:8]}", organization_id=svc_role.organization_id
    )
    session.add(group)
    await session.commit()
    group_id = group.id
    await _list(session, svc_role, preset.id, group_id=group_id)
    assert not await _allowed(session, svc_role, preset, editor)

    await session.execute(delete(Group).where(Group.id == group_id))
    await session.commit()

    # The cascade leaves no approvers, so the legacy rule applies again.
    assert await _allowed(session, svc_role, preset, editor)
    assert await _allowed(session, svc_role, preset, None)


def test_update_accepts_a_principal_named_twice() -> None:
    ref = {"kind": "user", "id": str(uuid.uuid4())}
    update = AgentPresetApproversUpdate.model_validate({"approvers": [ref, ref]})
    assert len(update.approvers) == 2


def test_update_caps_approvers_far_above_any_org_size() -> None:
    def refs(count: int) -> list[dict[str, str]]:
        return [{"kind": "user", "id": str(uuid.uuid4())} for _ in range(count)]

    assert MAX_AGENT_PRESET_APPROVERS == 10_000
    update = AgentPresetApproversUpdate.model_validate(
        {"approvers": refs(MAX_AGENT_PRESET_APPROVERS)}
    )
    assert len(update.approvers) == MAX_AGENT_PRESET_APPROVERS
    with pytest.raises(ValidationError):
        AgentPresetApproversUpdate.model_validate(
            {"approvers": refs(MAX_AGENT_PRESET_APPROVERS + 1)}
        )


async def test_db_keeps_one_principal_per_row_and_no_duplicates(
    session: AsyncSession, svc_role: Role
) -> None:
    preset_id = (await _preset(session, svc_role)).id
    user_id = (
        await _make_user(session, svc_role, workspace_role="workspace-editor")
    ).id
    editor_role_id = await _system_role_id(session, svc_role, "workspace-editor")

    for principal in ({}, {"user_id": user_id, "role_id": editor_role_id}):
        with pytest.raises(IntegrityError):
            await _list(session, svc_role, preset_id, **principal)
        await session.rollback()

    await _list(session, svc_role, preset_id, user_id=user_id)
    with pytest.raises(IntegrityError):
        await _list(session, svc_role, preset_id, user_id=user_id)
    await session.rollback()


async def test_deleting_a_principal_or_preset_removes_its_rows(
    session: AsyncSession, svc_role: Role
) -> None:
    preset = await _preset(session, svc_role)
    user = await _make_user(session, svc_role, workspace_role="workspace-editor")
    editor_role_id = await _system_role_id(session, svc_role, "workspace-editor")
    await _list(session, svc_role, preset.id, user_id=user.id)
    await _list(session, svc_role, preset.id, role_id=editor_role_id)

    await session.execute(delete(User).where(cast(Any, User.id) == user.id))
    await session.commit()
    remaining = (
        await session.scalars(
            select(AgentPresetApprover.role_id).where(
                AgentPresetApprover.preset_id == preset.id
            )
        )
    ).all()
    assert remaining == [editor_role_id]

    await session.execute(delete(AgentPreset).where(AgentPreset.id == preset.id))
    await session.commit()
    assert (
        await session.scalar(
            select(AgentPresetApprover.id).where(
                AgentPresetApprover.preset_id == preset.id
            )
        )
        is None
    )


async def test_approval_preset_stays_in_the_workspace_and_nulls_on_delete(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.organization_id is not None and svc_role.workspace_id is not None
    preset_id = (await _preset(session, svc_role)).id
    other = Workspace(name="Other", organization_id=svc_role.organization_id)
    session.add(other)
    await session.flush()

    def approval(workspace_id: uuid.UUID) -> Approval:
        return Approval(
            workspace_id=workspace_id,
            tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            tool_name="core.http_request",
            status=ApprovalStatus.PENDING,
            agent_preset_id=preset_id,
        )

    session.add(approval(other.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    stamped = approval(svc_role.workspace_id)
    session.add(stamped)
    await session.commit()
    await session.execute(delete(AgentPreset).where(AgentPreset.id == preset_id))
    await session.commit()
    await session.refresh(stamped)
    assert stamped.agent_preset_id is None
    assert stamped.workspace_id == svc_role.workspace_id


async def test_without_rbac_addons_grants_are_inert_and_unmanageable(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    preset = await _preset(session, svc_role)
    listed = await _make_user(session, svc_role, workspace_role="workspace-editor")
    outsider = await _make_user(session, svc_role, workspace_role="workspace-editor")
    await _list(session, svc_role, preset.id, user_id=listed.id)
    assert not await _allowed(session, svc_role, preset, outsider)

    _set_rbac_addons(monkeypatch, False)
    service = AgentPresetApproverService(session, role=svc_role)

    # The legacy rule decides; the stored grant is kept for a later upgrade.
    assert await _allowed(session, svc_role, preset, outsider)
    assert await _allowed(session, svc_role, preset, None)
    with pytest.raises(EntitlementRequired):
        await service.list_approvers(preset.id, PageParams(limit=10))
    with pytest.raises(EntitlementRequired):
        await service.list_options(PageParams(limit=10))
    with pytest.raises(EntitlementRequired):
        await service.add_approvers(preset.id, [])
    with pytest.raises(EntitlementRequired):
        await service.remove_approvers(preset.id, [])
    assert await session.scalar(
        select(AgentPresetApprover.id).where(AgentPresetApprover.preset_id == preset.id)
    )
