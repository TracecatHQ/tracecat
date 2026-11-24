"""Read, add, and remove who may approve an agent preset's tool calls."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    CompoundSelect,
    Select,
    Uuid,
    and_,
    delete,
    func,
    literal,
    or_,
    select,
    type_coerce,
    union,
    union_all,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import raiseload

from tracecat.agent.preset.schemas import (
    MAX_AGENT_PRESET_APPROVERS,
    AgentPresetApproverKind,
    AgentPresetApproverRead,
    AgentPresetApproverRef,
)
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPreset,
    AgentPresetApprover,
    Group,
    GroupRoleAssignment,
    Membership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers import WorkspaceID
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement

type _IdSelect = Select[Any] | CompoundSelect

# Every workspace has these roles, so they apply here without an assignment.
WORKSPACE_SYSTEM_ROLE_SLUGS = (
    "workspace-admin",
    "workspace-editor",
    "workspace-viewer",
)
# Organization roles are never workspace approvers, even if assigned here.
ORGANIZATION_SYSTEM_ROLE_SLUGS = (
    "organization-owner",
    "organization-admin",
    "organization-member",
)


@dataclass(frozen=True, slots=True)
class _Principals:
    """One id select per approver kind."""

    users: _IdSelect
    groups: _IdSelect
    roles: _IdSelect

    def union(self, other: _Principals) -> _Principals:
        return _Principals(
            users=union(self.users, other.users),
            groups=union(self.groups, other.groups),
            roles=union(self.roles, other.roles),
        )


def _preset_approvers(
    workspace_id: WorkspaceID, preset_id: uuid.UUID
) -> ColumnElement[bool]:
    """Match the preset's listed approvers."""
    return and_(
        AgentPresetApprover.workspace_id == workspace_id,
        AgentPresetApprover.preset_id == preset_id,
    )


def _user_read(user: User) -> AgentPresetApproverRead:
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return AgentPresetApproverRead(
        kind="user", id=user.id, name=full_name or user.email, email=user.email
    )


def _requested(
    approvers: Sequence[AgentPresetApproverRef], kind: AgentPresetApproverKind
) -> list[uuid.UUID]:
    return [ref.id for ref in approvers if ref.kind == kind]


class AgentPresetApproverService(BaseWorkspaceService):
    """The preset-wide approver set: live workspace policy, never versioned.

    Enterprise: every method needs the ``rbac_addons`` entitlement.
    """

    service_name = "agent_preset_approver"

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.RBAC_ADDONS)
    async def list_approvers(
        self, preset_id: uuid.UUID, page: PageParams
    ) -> CursorPaginatedResponse[AgentPresetApproverRead]:
        """A page of the preset's approvers: users by email, then groups, then roles.

        Raises:
            PaginationError: If the cursor is invalid.
        """
        return await self._page(self._listed(preset_id), page)

    @require_scope("agent:update", "workspace:member:read")
    @requires_entitlement(Entitlement.RBAC_ADDONS)
    async def list_options(
        self, page: PageParams, *, search: str | None = None
    ) -> CursorPaginatedResponse[AgentPresetApproverRead]:
        """A page of the principals that already apply in this workspace.

        Raises:
            PaginationError: If the cursor is invalid.
        """
        return await self._page(self._options(), page, search=search)

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.RBAC_ADDONS)
    @audit_log(
        resource_type="agent_preset", action="update", resource_id_attr="preset_id"
    )
    async def add_approvers(
        self, preset_id: uuid.UUID, approvers: Sequence[AgentPresetApproverRef]
    ) -> list[AgentPresetApproverRead]:
        """Add approvers; already-listed ones and repeats are no-ops.

        Raises:
            TracecatNotFoundError: If the preset is not in this workspace.
            TracecatValidationError: If an approver does not apply in this
                workspace, or the preset would exceed the approver cap.
        """
        approvers = list(dict.fromkeys(approvers))
        await self._lock_preset(preset_id)
        if not approvers:
            await self.session.commit()
            return []
        valid = await self._named(
            self._options().union(self._listed(preset_id)), only=approvers
        )
        if {(read.kind, read.id) for read in valid} != {
            (ref.kind, ref.id) for ref in approvers
        }:
            raise TracecatValidationError(
                "Approvers must be members, groups, or roles of this workspace"
            )
        # executemany: SQLAlchemy pages the rows under the driver's bind limit.
        await self.session.execute(
            pg_insert(AgentPresetApprover).on_conflict_do_nothing(),
            [
                {
                    "workspace_id": self.workspace_id,
                    "preset_id": preset_id,
                    "user_id": ref.id if ref.kind == "user" else None,
                    "group_id": ref.id if ref.kind == "group" else None,
                    "role_id": ref.id if ref.kind == "role" else None,
                }
                for ref in approvers
            ],
        )
        listed = await self.session.scalar(
            select(func.count()).where(_preset_approvers(self.workspace_id, preset_id))
        )
        if (listed or 0) > MAX_AGENT_PRESET_APPROVERS:
            await self.session.rollback()
            raise TracecatValidationError(
                f"An agent can list at most {MAX_AGENT_PRESET_APPROVERS:,} approvers"
            )
        await self.session.commit()
        return valid

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.RBAC_ADDONS)
    @audit_log(
        resource_type="agent_preset", action="update", resource_id_attr="preset_id"
    )
    async def remove_approvers(
        self, preset_id: uuid.UUID, approvers: Sequence[AgentPresetApproverRef]
    ) -> None:
        """Remove approvers; ones not listed are no-ops. Removing all restores the default rule.

        Raises:
            TracecatNotFoundError: If the preset is not in this workspace.
        """
        await self._lock_preset(preset_id)
        if approvers:
            await self.session.execute(
                delete(AgentPresetApprover).where(
                    _preset_approvers(self.workspace_id, preset_id),
                    or_(
                        AgentPresetApprover.user_id.in_(_requested(approvers, "user")),
                        AgentPresetApprover.group_id.in_(
                            _requested(approvers, "group")
                        ),
                        AgentPresetApprover.role_id.in_(_requested(approvers, "role")),
                    ),
                )
            )
        await self.session.commit()

    async def _lock_preset(self, preset_id: uuid.UUID) -> None:
        # FOR NO KEY UPDATE: serializes changes without blocking FK checks.
        preset = await self.session.scalar(
            select(AgentPreset.id)
            .where(
                AgentPreset.id == preset_id,
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
            .with_for_update(key_share=True)
        )
        if preset is None:
            raise TracecatNotFoundError(f"Agent preset {preset_id} not found")

    async def _page(
        self,
        principals: _Principals,
        page: PageParams,
        *,
        search: str | None = None,
    ) -> CursorPaginatedResponse[AgentPresetApproverRead]:
        """Page the principals: users by email, then groups, then roles by name."""
        user_id, email = cast(Any, User.id), cast(Any, User.email)
        users = select(
            literal("user").label("kind"),
            literal(0).label("rank"),
            email.label("sort_key"),
            # The union's first select types its cursor columns.
            type_coerce(user_id, Uuid).label("id"),
        ).where(user_id.in_(principals.users))
        groups = select(literal("group"), literal(1), Group.name, Group.id).where(
            Group.id.in_(principals.groups)
        )
        roles = select(literal("role"), literal(2), DBRole.name, DBRole.id).where(
            DBRole.id.in_(principals.roles)
        )
        if search:
            users = users.where(
                or_(
                    email.icontains(search, autoescape=True),
                    cast(Any, User.first_name).icontains(search, autoescape=True),
                    cast(Any, User.last_name).icontains(search, autoescape=True),
                )
            )
            groups = groups.where(Group.name.icontains(search, autoescape=True))
            roles = roles.where(DBRole.name.icontains(search, autoescape=True))
        rows = union_all(users, groups, roles).subquery()
        result = await paginate(
            self.session,
            select(rows.c.kind, rows.c.id),
            page=page,
            order_by=(rows.c.rank.asc(), rows.c.sort_key.asc(), rows.c.id.asc()),
            row_factory=lambda values: AgentPresetApproverRef.model_validate(
                {"kind": values[0], "id": values[1]}
            ),
        )
        named = (
            {
                (read.kind, read.id): read
                for read in await self._named(principals, only=result.items)
            }
            if result.items
            else {}
        )
        return CursorPaginatedResponse(
            items=[
                named[ref.kind, ref.id]
                for ref in result.items
                if (ref.kind, ref.id) in named
            ],
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
        )

    def _listed(self, preset_id: uuid.UUID) -> _Principals:
        listed = _preset_approvers(self.workspace_id, preset_id)
        return _Principals(
            users=select(AgentPresetApprover.user_id).where(listed),
            groups=select(AgentPresetApprover.group_id).where(listed),
            roles=select(AgentPresetApprover.role_id).where(listed),
        )

    def _options(self) -> _Principals:
        """Principals granted in this workspace; org-wide grants never count."""
        workspace_role = and_(
            DBRole.organization_id == self.organization_id,
            # Custom roles have no slug; NOT IN alone would drop them.
            or_(
                DBRole.slug.is_(None),
                DBRole.slug.not_in(ORGANIZATION_SYSTEM_ROLE_SLUGS),
            ),
        )
        # A group granted only an organization role here is not an option.
        group_grants = select(GroupRoleAssignment).where(
            GroupRoleAssignment.organization_id == self.organization_id,
            GroupRoleAssignment.workspace_id == self.workspace_id,
            GroupRoleAssignment.role_id.in_(select(DBRole.id).where(workspace_role)),
        )
        user_grants = select(UserRoleAssignment.role_id).where(
            UserRoleAssignment.organization_id == self.organization_id,
            UserRoleAssignment.workspace_id == self.workspace_id,
        )
        return _Principals(
            users=select(Membership.user_id).where(
                Membership.workspace_id == self.workspace_id
            ),
            groups=group_grants.with_only_columns(GroupRoleAssignment.group_id),
            roles=select(DBRole.id).where(
                workspace_role,
                or_(
                    DBRole.slug.in_(WORKSPACE_SYSTEM_ROLE_SLUGS),
                    DBRole.id.in_(user_grants),
                    DBRole.id.in_(
                        group_grants.with_only_columns(GroupRoleAssignment.role_id)
                    ),
                ),
            ),
        )

    async def _named(
        self,
        principals: _Principals,
        *,
        only: Sequence[AgentPresetApproverRef],
    ) -> list[AgentPresetApproverRead]:
        """Name the principals in ``only``: users by email, then groups, roles."""
        user_id = cast(Any, User.id)
        users = (
            select(User)
            .where(
                user_id.in_(principals.users),
                user_id.in_(_requested(only, "user")),
            )
            .options(raiseload(cast(Any, User.oauth_accounts)))
        )
        groups = select(Group).where(
            Group.id.in_(principals.groups), Group.id.in_(_requested(only, "group"))
        )
        roles = select(DBRole).where(
            DBRole.id.in_(principals.roles), DBRole.id.in_(_requested(only, "role"))
        )
        return [
            *map(_user_read, await self.session.scalars(users.order_by(User.email))),
            *(
                AgentPresetApproverRead(kind="group", id=group.id, name=group.name)
                for group in await self.session.scalars(groups.order_by(Group.name))
            ),
            *(
                AgentPresetApproverRead(kind="role", id=role.id, name=role.name)
                for role in await self.session.scalars(roles.order_by(DBRole.name))
            ),
        ]
