"""Read and replace who may approve an agent preset's tool calls."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CompoundSelect, Select, delete, select, union
from sqlalchemy.orm import raiseload

from tracecat.agent.approvals.policy import preset_approvers
from tracecat.agent.preset.schemas import (
    AgentPresetApproverKind,
    AgentPresetApproverRead,
    AgentPresetApproverRef,
)
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.authz.scopes import ORG_MEMBER_ROLE_SLUG
from tracecat.db.models import (
    AgentPreset,
    AgentPresetApprover,
    Group,
    Membership,
    User,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService

type _IdSelect = Select[Any] | CompoundSelect


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
    """The preset-wide approver set: live workspace policy, never versioned."""

    service_name = "agent_preset_approver"

    @require_scope("agent:read")
    async def list_approvers(
        self, preset_id: uuid.UUID
    ) -> list[AgentPresetApproverRead]:
        """List a preset's approvers: users by email, then groups, then roles."""
        return await self._named(self._listed(preset_id))

    @require_scope("agent:read", "workspace:member:read")
    async def list_options(self) -> list[AgentPresetApproverRead]:
        """List the workspace members, org groups, and org roles one can pick."""
        return await self._named(self._options())

    @require_scope("agent:update")
    @audit_log(
        resource_type="agent_preset", action="update", resource_id_attr="preset_id"
    )
    async def replace_approvers(
        self, preset_id: uuid.UUID, approvers: Sequence[AgentPresetApproverRef]
    ) -> list[AgentPresetApproverRead]:
        """Replace the preset-wide approver set; an empty set clears the policy.

        Approvers already listed may stay even after leaving the workspace.

        Raises:
            TracecatNotFoundError: If the preset is not in this workspace.
            TracecatValidationError: If a new approver is not a workspace
                member, org group, or org role.
        """
        # Serializes replacements without blocking FK checks against the preset.
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
        valid = (
            await self._named(
                self._options().union(self._listed(preset_id)), only=approvers
            )
            if approvers
            else []
        )
        if len(valid) != len(approvers):
            raise TracecatValidationError(
                "Approvers must be workspace members, groups, or roles"
            )
        await self.session.execute(
            delete(AgentPresetApprover).where(
                preset_approvers(self.workspace_id, preset_id)
            )
        )
        self.session.add_all(
            AgentPresetApprover(
                workspace_id=self.workspace_id,
                preset_id=preset_id,
                user_id=ref.id if ref.kind == "user" else None,
                group_id=ref.id if ref.kind == "group" else None,
                role_id=ref.id if ref.kind == "role" else None,
            )
            for ref in approvers
        )
        await self.session.commit()
        return valid

    def _listed(self, preset_id: uuid.UUID) -> _Principals:
        listed = preset_approvers(self.workspace_id, preset_id)
        return _Principals(
            users=select(AgentPresetApprover.user_id).where(listed),
            groups=select(AgentPresetApprover.group_id).where(listed),
            roles=select(AgentPresetApprover.role_id).where(listed),
        )

    def _options(self) -> _Principals:
        return _Principals(
            users=select(Membership.user_id).where(
                Membership.workspace_id == self.workspace_id
            ),
            groups=select(Group.id).where(
                Group.organization_id == self.organization_id
            ),
            roles=select(DBRole.id).where(
                DBRole.organization_id == self.organization_id,
                DBRole.slug.is_distinct_from(ORG_MEMBER_ROLE_SLUG),
            ),
        )

    async def _named(
        self,
        principals: _Principals,
        *,
        only: Sequence[AgentPresetApproverRef] | None = None,
    ) -> list[AgentPresetApproverRead]:
        """Name the principals, narrowed to ``only``: users by email, then groups, roles."""
        user_id = cast(Any, User.id)
        users = (
            select(User)
            .where(user_id.in_(principals.users))
            .options(raiseload(cast(Any, User.oauth_accounts)))
        )
        groups = select(Group).where(Group.id.in_(principals.groups))
        roles = select(DBRole).where(DBRole.id.in_(principals.roles))
        if only is not None:
            users = users.where(user_id.in_(_requested(only, "user")))
            groups = groups.where(Group.id.in_(_requested(only, "group")))
            roles = roles.where(DBRole.id.in_(_requested(only, "role")))
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
