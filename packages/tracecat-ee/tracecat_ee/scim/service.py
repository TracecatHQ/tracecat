"""Directory synchronization from an external identity provider.

The identity provider owns which users are in which external group; Tracecat
owns what a group grants. External groups never enter RBAC directly: an
admin-authored mapping is read live by the IdP arm of the role-path union, so
no ``group_member`` rows are projected and nothing needs recomputing.

Deprovisioning is org-scoped removal, not global deactivation. ``active=false``
from one tenant's provider must not reach that user's other organizations, so it
clears ``external_user.active`` and delegates removal to
``OrgService.delete_member`` rather than writing ``is_active``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.authz.controls import require_scope
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import ensure_member, lock_role_changes
from tracecat.db.models import (
    ExternalGroup,
    ExternalGroupMapping,
    ExternalGroupMember,
    ExternalUser,
    Group,
    GroupMember,
    Invitation,
    OrganizationMembership,
    ScimConnection,
    User,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.service import OrgService
from tracecat.pagination import Page, PageParams, paginate
from tracecat.service import BaseOrgService
from tracecat_ee.scim.schemas import (
    ExternalGroupMappingCreate,
    ExternalGroupMappingRead,
    ExternalGroupRead,
    ScimActivationReviewRead,
    ScimDirectoryUserRead,
    ScimMappingPlanRead,
)


class SCIMService(BaseOrgService):
    """Syncs external directory state into Tracecat groups and membership."""

    service_name = "scim"

    # =========================================================================
    # Read-side operations for the admin mapping API
    # =========================================================================

    @require_scope("org:rbac:read")
    async def list_external_groups(
        self, *, page: PageParams
    ) -> Page[ExternalGroupRead]:
        """List this organization's synced IdP groups with their member counts.

        Returns:
            A bounded page of synced groups, ordered by display name and ID.
        """
        # Correlated scalar subquery rather than an outer join plus group_by:
        # it keeps groups with no members at zero without a coalesce, and the
        # selected columns stay a flat projection.
        member_count = (
            select(func.count())
            .select_from(ExternalGroupMember)
            .where(ExternalGroupMember.external_group_id == ExternalGroup.id)
            .scalar_subquery()
        )
        stmt = select(
            ExternalGroup.id,
            ExternalGroup.external_id,
            ExternalGroup.display_name,
            member_count.label("member_count"),
        ).where(ExternalGroup.organization_id == self.organization_id)
        result = await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(ExternalGroup.display_name.asc(), ExternalGroup.id.asc()),
            row_factory=lambda row: ExternalGroupRead.model_validate(
                dict(
                    zip(
                        ("id", "external_id", "display_name", "member_count"),
                        row,
                        strict=True,
                    )
                )
            ),
        )
        return result

    @require_scope("org:rbac:read")
    async def get_mapping(self, mapping_id: UUID) -> ExternalGroupMappingRead:
        """Read one mapping with both sides joined in.

        Args:
            mapping_id: The mapping to read.

        Returns:
            The mapping and the display detail for both of its sides.

        Raises:
            TracecatNotFoundError: The mapping is not in this organization.
        """
        rows = await self._mapping_rows(ExternalGroupMapping.id == mapping_id)
        if not rows:
            raise TracecatNotFoundError("External group mapping not found")
        return rows[0]

    @require_scope("org:rbac:read")
    async def list_mappings(self) -> list[ExternalGroupMappingRead]:
        """List this organization's mappings with both sides joined in.

        Returns:
            Every mapping, ordered by external then Tracecat group name.
        """
        return await self._mapping_rows()

    async def _mapping_rows(
        self, *criteria: ColumnElement[bool]
    ) -> list[ExternalGroupMappingRead]:
        """Read mappings joined to both sides, always org-scoped."""
        stmt = (
            select(
                ExternalGroupMapping.id,
                ExternalGroupMapping.external_group_id,
                ExternalGroup.external_id,
                ExternalGroup.display_name,
                ExternalGroupMapping.group_id,
                Group.name,
            )
            .join(
                ExternalGroup,
                ExternalGroup.id == ExternalGroupMapping.external_group_id,
            )
            .join(Group, Group.id == ExternalGroupMapping.group_id)
            .where(
                ExternalGroupMapping.organization_id == self.organization_id, *criteria
            )
            .order_by(ExternalGroup.display_name, Group.name, ExternalGroupMapping.id)
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return [
            ExternalGroupMappingRead(
                id=mapping_id,
                external_group_id=external_group_id,
                external_group_external_id=external_id,
                external_group_display_name=display_name,
                group_id=group_id,
                group_name=group_name,
            )
            for (
                mapping_id,
                external_group_id,
                external_id,
                display_name,
                group_id,
                group_name,
            ) in rows
        ]

    # =========================================================================
    # Write-side operations for the sync endpoints
    # =========================================================================

    @audit_log(
        resource_type="scim_directory",
        action="sync",
        resource_id_attr="id",
    )
    async def upsert_external_group(
        self, *, external_id: str, display_name: str
    ) -> ExternalGroup:
        """Create or rename a synced external group.

        Args:
            external_id: The provider's identifier for the group.
            display_name: The provider's current display name.

        Returns:
            The stored external group.
        """
        await lock_role_changes(self.session, self.organization_id)
        stmt = (
            pg_insert(ExternalGroup)
            .values(
                organization_id=self.organization_id,
                external_id=external_id,
                display_name=display_name,
            )
            .on_conflict_do_update(
                index_elements=[
                    ExternalGroup.organization_id,
                    ExternalGroup.external_id,
                ],
                set_={"display_name": display_name},
            )
            .returning(ExternalGroup)
        )
        external_group = (await self.session.execute(stmt)).scalar_one()
        # Renaming grants nothing, so no group needs reconciling here.
        return external_group

    async def update_external_group(
        self, group: ExternalGroup, *, external_id: str | None, display_name: str
    ) -> None:
        """Update the addressed group without replacing its stable resource ID."""
        await lock_role_changes(self.session, self.organization_id)
        if external_id is not None and external_id != group.external_id:
            duplicate = await self.session.scalar(
                select(ExternalGroup.id).where(
                    ExternalGroup.organization_id == self.organization_id,
                    ExternalGroup.external_id == external_id,
                    ExternalGroup.id != group.id,
                )
            )
            if duplicate is not None:
                raise TracecatConflictError(
                    "An external group already uses this externalId"
                )
            group.external_id = external_id
        group.display_name = display_name
        await self.session.flush()

    @audit_log(
        resource_type="scim_directory",
        action="sync",
        resource_id_attr="external_group_id",
    )
    async def delete_external_group(self, external_group_id: UUID) -> None:
        """Delete a synced external group and drop what it supplied.

        Args:
            external_group_id: The external group to remove.

        Raises:
            TracecatNotFoundError: The external group is not in this organization.
        """
        await lock_role_changes(self.session, self.organization_id)
        # Target groups first, then the external group: delete_mapping takes
        # the group lock before touching mapping rows, and the reverse order
        # here would let the two wait on each other.
        await self._get_external_group(external_group_id)
        for group_id in sorted(set(await self._mapped_group_ids(external_group_id))):
            await self._lock_group(group_id)

        # Re-read under lock: a mapping created concurrently would otherwise
        # supply members this deletion never reconciles.
        external_group = await self._get_external_group(
            external_group_id, for_update=True
        )
        # Provider deletion revokes this source's access. Only an explicit
        # administrator unmapping hands ownership back to manual membership.
        await self.session.delete(external_group)
        await self.session.flush()

    @audit_log(
        resource_type="scim_directory",
        action="sync",
        resource_id_attr="external_group_id",
    )
    async def replace_external_group_members(
        self, external_group_id: UUID, external_user_ids: Sequence[UUID]
    ) -> None:
        """Replace an external group's member list with the provider's.

        Args:
            external_group_id: The external group being synced.
            external_user_ids: The complete member list as pushed by the
                provider, as ``external_user`` row ids.

        Raises:
            TracecatNotFoundError: The external group is not in this organization.
        """
        await lock_role_changes(self.session, self.organization_id)
        # Serializes concurrent replacements: without it two pushes can each
        # delete nothing and insert independently, leaving their union.
        await self._get_external_group(external_group_id, for_update=True)
        desired = set(external_user_ids)

        stale = delete(ExternalGroupMember).where(
            ExternalGroupMember.external_group_id == external_group_id
        )
        if desired:
            stale = stale.where(ExternalGroupMember.external_user_id.not_in(desired))
        await self.session.execute(stale)

        if desired:
            await self.session.execute(
                pg_insert(ExternalGroupMember)
                .values(
                    [
                        {
                            "organization_id": self.organization_id,
                            "external_group_id": external_group_id,
                            "external_user_id": external_user_id,
                        }
                        for external_user_id in sorted(desired, key=str)
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ExternalGroupMember.external_group_id,
                        ExternalGroupMember.external_user_id,
                    ]
                )
            )
        await self.session.flush()

    @require_scope("org:rbac:create")
    @audit_log(
        resource_type="scim_group_mapping",
        action="create",
        resource_id_attr="id",
    )
    async def create_mapping(
        self, *, external_group_id: UUID, group_id: UUID
    ) -> ExternalGroupMapping:
        """Map a synced external group into a Tracecat group.

        Args:
            external_group_id: The synced source group.
            group_id: The Tracecat group whose scopes its members receive.

        Returns:
            The stored mapping.

        Raises:
            TracecatNotFoundError: Either side is not in this organization.
        """
        await lock_role_changes(self.session, self.organization_id)
        await self._get_external_group(external_group_id)
        await self._lock_group(group_id)
        if not await self._connection_is_active():
            raise TracecatConflictError("Activate SCIM before creating group mappings")

        stmt = (
            pg_insert(ExternalGroupMapping)
            .values(
                organization_id=self.organization_id,
                external_group_id=external_group_id,
                group_id=group_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ExternalGroupMapping.external_group_id,
                    ExternalGroupMapping.group_id,
                ]
            )
            .returning(ExternalGroupMapping)
        )
        mapping = (await self.session.execute(stmt)).scalar_one_or_none()
        if mapping is None:
            mapping = await self._get_mapping(
                external_group_id=external_group_id, group_id=group_id
            )
        # The IdP owns a mapped group's membership, so hand-added rows would be
        # invisible in the UI yet still grant access.
        await self._purge_manual_members(group_id)
        await self.session.flush()
        return mapping

    @require_scope("org:rbac:delete")
    @audit_log(
        resource_type="scim_group_mapping",
        action="delete",
        resource_id_attr="mapping_id",
    )
    async def delete_mapping(self, mapping_id: UUID) -> None:
        """Remove a mapping and drop the membership only it supplied.

        Args:
            mapping_id: The mapping to remove.

        Raises:
            TracecatNotFoundError: The mapping is not in this organization.
        """
        await lock_role_changes(self.session, self.organization_id)
        stmt = select(ExternalGroupMapping).where(
            ExternalGroupMapping.id == mapping_id,
            ExternalGroupMapping.organization_id == self.organization_id,
        )
        mapping = (await self.session.execute(stmt)).scalar_one_or_none()
        if mapping is None:
            raise TracecatNotFoundError("External group mapping not found")

        group_id = mapping.group_id
        # Locked before the delete: create_mapping locks the group first, so
        # the reverse order here would let the two wait on each other.
        await self._lock_group(group_id)
        # Unmapping the last source would drop every member at once, so the
        # current IdP membership is frozen as manual rows first.
        if await self._is_last_mapping(group_id, mapping.external_group_id):
            await self._freeze_idp_members_as_manual(group_id)
        await self.session.delete(mapping)
        await self.session.flush()

    # =========================================================================
    # Activation review
    # =========================================================================

    @require_scope("org:rbac:read")
    async def review_activation(
        self, proposed: Sequence[ExternalGroupMappingCreate]
    ) -> ScimActivationReviewRead:
        """Report what the provider pushed and what activating would do.

        A plain read: nothing about the returned plan is stored, so a stale
        review can only be acted on by re-running the activation itself.

        Args:
            proposed: The mappings the admin intends to install.

        Returns:
            The pushed users and one plan per proposed mapping.
        """
        users = await self._directory_users()
        plans = [
            await self._mapping_plan(
                external_group_id=m.external_group_id, group_id=m.group_id
            )
            for m in proposed
        ]
        return ScimActivationReviewRead(users=users, plans=plans)

    @require_scope("org:rbac:update", "org:member:remove")
    @audit_log(resource_type="scim_connection", action="update")
    async def activate(self, proposed: Sequence[ExternalGroupMappingCreate]) -> None:
        """Admit the pushed directory and install the reviewed mappings.

        One transaction: admission, mappings and the status flip land together,
        so a failure cannot leave the connection active with nothing admitted.

        Args:
            proposed: The mappings to install as part of activation.

        Raises:
            TracecatNotFoundError: No connection exists, or a mapping side is
                not in this organization.
        """
        await lock_role_changes(self.session, self.organization_id)
        connection = (
            await self.session.execute(
                select(ScimConnection)
                .where(ScimConnection.organization_id == self.organization_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if connection is None:
            raise TracecatNotFoundError("SCIM connection not found")

        # Status first: admission and mapping installation below read it.
        connection.status = ScimConnectionStatus.ACTIVE
        self.session.add(connection)
        await self.session.flush()

        await self._admit_pushed_users()
        for mapping in proposed:
            await self.create_mapping(
                external_group_id=mapping.external_group_id,
                group_id=mapping.group_id,
            )
        await self.session.commit()

    async def _admit_pushed_users(self) -> None:
        """Reconcile staged users before making the directory authoritative."""
        inactive_ids = (
            await self.session.scalars(
                select(ExternalUser.user_id).where(
                    ExternalUser.organization_id == self.organization_id,
                    ExternalUser.active.is_(False),
                )
            )
        ).all()
        for user_id in inactive_ids:
            await self.deprovision_user(user_id, commit=False)

        active_emails = (
            select(func.lower(User.email))
            .join(ExternalUser, ExternalUser.user_id == User.id)
            .where(
                ExternalUser.organization_id == self.organization_id,
                ExternalUser.active,
            )
        )
        await self.session.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == self.organization_id,
                Invitation.status == InvitationStatus.PENDING,
                func.lower(Invitation.email).in_(active_emails),
            )
            .values(status=InvitationStatus.REVOKED)
        )
        await self.session.execute(
            pg_insert(OrganizationMembership)
            .from_select(
                ["organization_id", "user_id"],
                select(ExternalUser.organization_id, ExternalUser.user_id).where(
                    ExternalUser.organization_id == self.organization_id,
                    ExternalUser.active,
                ),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    OrganizationMembership.organization_id,
                    OrganizationMembership.user_id,
                ]
            )
        )
        await self.session.flush()

    async def _revoke_pending_invitation(self, email: str) -> None:
        """Revoke a live invitation whose role could outrank what SCIM grants."""
        await self.session.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == self.organization_id,
                func.lower(Invitation.email) == email.lower(),
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.REVOKED)
        )

    async def _directory_users(self) -> list[ScimDirectoryUserRead]:
        """The users the provider has pushed into this organization."""
        rows = (
            await self.session.execute(
                select(  # pyright: ignore[reportCallIssue]
                    ExternalUser.id,
                    User.email,  # pyright: ignore[reportArgumentType]
                    ExternalUser.external_id,
                    ExternalUser.active,
                )
                .join(User, User.id == ExternalUser.user_id)  # pyright: ignore[reportArgumentType]
                .where(ExternalUser.organization_id == self.organization_id)
                .order_by(User.email)
            )
        ).tuples()
        return [
            ScimDirectoryUserRead(
                id=row_id, email=email, external_id=external_id, active=active
            )
            for row_id, email, external_id, active in rows
        ]

    async def _mapping_plan(
        self, *, external_group_id: UUID, group_id: UUID
    ) -> ScimMappingPlanRead:
        """What installing one mapping would change for a Tracecat group."""
        external_group = await self._get_external_group(external_group_id)
        group_name = (
            await self.session.execute(
                select(Group.name).where(
                    Group.id == group_id,
                    Group.organization_id == self.organization_id,
                )
            )
        ).scalar_one_or_none()
        if group_name is None:
            raise TracecatNotFoundError("Group not found")

        manual_emails = dict(
            (
                await self.session.execute(
                    select(User.__table__.c.id, User.__table__.c.email)
                    .join(GroupMember, GroupMember.user_id == User.__table__.c.id)
                    .where(GroupMember.group_id == group_id)
                )
            )
            .tuples()
            .all()
        )
        manual = set(manual_emails)
        incoming = await self._external_group_user_ids(external_group_id)
        already = await self._idp_member_ids(group_id)
        return ScimMappingPlanRead(
            external_group_id=external_group_id,
            external_group_display_name=external_group.display_name,
            group_id=group_id,
            group_name=group_name,
            manual_members_purged=sorted(manual, key=str),
            manual_member_emails=manual_emails,
            users_gaining_access=sorted(incoming - manual - already, key=str),
            users_losing_access=sorted(manual - incoming - already, key=str),
        )

    async def _manual_member_ids(self, group_id: UUID) -> set[UUID]:
        """Users held by a stored group_member row."""
        stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
        return set((await self.session.execute(stmt)).scalars())

    async def _external_group_user_ids(self, external_group_id: UUID) -> set[UUID]:
        """Active users the provider lists in one external group."""
        stmt = (
            select(ExternalUser.user_id)
            .join(
                ExternalGroupMember,
                ExternalGroupMember.external_user_id == ExternalUser.id,
            )
            .where(
                ExternalGroupMember.external_group_id == external_group_id,
                ExternalUser.active,
            )
        )
        return set((await self.session.execute(stmt)).scalars())

    async def _idp_member_ids(self, group_id: UUID) -> set[UUID]:
        """Users an already-installed mapping projects into the group."""
        stmt = (
            select(ExternalUser.user_id)
            .join(
                ExternalGroupMember,
                ExternalGroupMember.external_user_id == ExternalUser.id,
            )
            .join(
                ExternalGroupMapping,
                ExternalGroupMapping.external_group_id
                == ExternalGroupMember.external_group_id,
            )
            .where(
                ExternalGroupMapping.group_id == group_id,
                ExternalGroupMapping.organization_id == self.organization_id,
                ExternalUser.active,
            )
        )
        return set((await self.session.execute(stmt)).scalars())

    # =========================================================================
    # Deprovisioning
    # =========================================================================

    async def deprovision_user(self, user_id: UUID, *, commit: bool = True) -> None:
        """Remove a user from this organization at the provider's instruction.

        ``active=false`` revokes access to this tenant only: the row and its
        group links are kept so re-activation relinks the same resource id, and
        the global ``is_active`` flag is never written. Clearing ``active``
        already drops the IdP role-path arm; ``delete_member`` then removes the
        membership row and the direct and manual paths with it.
        Before connection activation, only the staged active flag changes.

        Args:
            user_id: The user the provider has deprovisioned.
            commit: Commit standalone pushes; activation owns its transaction.

        Raises:
            TracecatAuthorizationError: The user is a superuser, or the caller
                lacks ``org:member:remove``.
            TracecatNotFoundError: The account no longer exists.
        """
        await lock_role_changes(self.session, self.organization_id)
        if not await self._connection_is_active():
            # Before activation the provider owns only the staged directory.
            await self.deactivate_external_user(user_id)
            if commit:
                await self.session.commit()
            return
        user = await self.session.get(User, user_id)
        if user is None:
            raise TracecatNotFoundError("User not found")
        if user.is_superuser:
            raise TracecatAuthorizationError("Cannot delete superuser")
        await self.deactivate_external_user(user_id)
        membership = await self.session.get(
            OrganizationMembership, (user_id, self.organization_id)
        )
        # An already absent membership must not revoke fresh sessions in other
        # organizations on every provider retry. Restored admission still needs cleanup.
        if membership is not None:
            await OrgService(self.session, self.role).delete_member(
                user_id, allow_idp_managed=True, member=user, commit=False
            )
        if commit:
            await self.session.commit()

    async def reactivate_external_user(self, external_user: ExternalUser) -> None:
        """Re-admit a user the provider has activated again.

        Admission still waits on the connection being active: a pending
        connection collects the directory without granting anything.
        """
        await lock_role_changes(self.session, self.organization_id)
        await self.session.execute(
            update(ExternalUser)
            .where(ExternalUser.id == external_user.id)
            .values(active=True)
        )
        if await self._connection_is_active():
            email = await self.session.scalar(
                select(User.__table__.c.email).where(
                    User.__table__.c.id == external_user.user_id
                )
            )
            if email is not None:
                await self._revoke_pending_invitation(email)
            await ensure_member(
                self.session, self.organization_id, external_user.user_id
            )
        await self.session.flush()

    async def _connection_is_active(self) -> bool:
        """Whether this organization's connection has been activated."""
        stmt = select(ScimConnection.status).where(
            ScimConnection.organization_id == self.organization_id
        )
        return (
            await self.session.execute(stmt)
        ).scalar_one_or_none() == ScimConnectionStatus.ACTIVE

    async def deactivate_external_user(self, user_id: UUID) -> None:
        """Clear the active flag, keeping the row and its group links."""
        await lock_role_changes(self.session, self.organization_id)
        await self.session.execute(
            update(ExternalUser)
            .where(
                ExternalUser.user_id == user_id,
                ExternalUser.organization_id == self.organization_id,
            )
            .values(active=False)
        )
        await self.session.flush()

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _lock_group(self, group_id: UUID) -> None:
        """Lock the group so concurrent reconciles serialize on it.

        Groups are locked before any user row, matching the order RBAC's
        ``_sync_group_memberships`` takes, so the two cannot deadlock.
        """
        stmt = (
            select(Group.id)
            .where(Group.id == group_id, Group.organization_id == self.organization_id)
            .with_for_update()
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            raise TracecatNotFoundError("Group not found")

    async def _get_external_group(
        self, external_group_id: UUID, *, for_update: bool = False
    ) -> ExternalGroup:
        stmt = select(ExternalGroup).where(
            ExternalGroup.id == external_group_id,
            ExternalGroup.organization_id == self.organization_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        external_group = (await self.session.execute(stmt)).scalar_one_or_none()
        if external_group is None:
            raise TracecatNotFoundError("External group not found")
        return external_group

    async def _get_mapping(
        self, *, external_group_id: UUID, group_id: UUID
    ) -> ExternalGroupMapping:
        stmt = select(ExternalGroupMapping).where(
            ExternalGroupMapping.external_group_id == external_group_id,
            ExternalGroupMapping.group_id == group_id,
            ExternalGroupMapping.organization_id == self.organization_id,
        )
        mapping = (await self.session.execute(stmt)).scalar_one_or_none()
        if mapping is None:
            raise TracecatNotFoundError("External group mapping not found")
        return mapping

    async def _mapped_group_ids(self, external_group_id: UUID) -> list[UUID]:
        stmt = select(ExternalGroupMapping.group_id).where(
            ExternalGroupMapping.external_group_id == external_group_id,
            ExternalGroupMapping.organization_id == self.organization_id,
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _is_last_mapping(self, group_id: UUID, external_group_id: UUID) -> bool:
        """Whether this is the only external group still mapped into the group."""
        stmt = select(ExternalGroupMapping.id).where(
            ExternalGroupMapping.group_id == group_id,
            ExternalGroupMapping.organization_id == self.organization_id,
            ExternalGroupMapping.external_group_id != external_group_id,
        )
        return (await self.session.execute(stmt)).first() is None

    async def _freeze_idp_members_as_manual(self, group_id: UUID) -> None:
        """Copy the group's current IdP members in as manual rows.

        Losing its last mapping would otherwise revoke every member's access at
        once; the admin keeps the membership and can edit it by hand again.
        """
        # Joined through the membership row: it is the aggregate root the
        # group_member insert below hangs off, and a user the provider pushed
        # while the connection was pending has none.
        members = (
            select(ExternalUser.user_id)
            .join(
                ExternalGroupMember,
                ExternalGroupMember.external_user_id == ExternalUser.id,
            )
            .join(
                ExternalGroupMapping,
                ExternalGroupMapping.external_group_id
                == ExternalGroupMember.external_group_id,
            )
            .join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == ExternalUser.user_id,
                    OrganizationMembership.organization_id
                    == ExternalUser.organization_id,
                ),
            )
            .where(
                ExternalGroupMapping.group_id == group_id,
                ExternalGroupMapping.organization_id == self.organization_id,
                ExternalUser.active,
            )
            .distinct()
        )
        user_ids = list((await self.session.execute(members)).scalars())
        if not user_ids:
            return
        await self.session.execute(
            pg_insert(GroupMember)
            .values(
                [
                    {
                        "group_id": group_id,
                        "user_id": user_id,
                        "organization_id": self.organization_id,
                    }
                    for user_id in sorted(user_ids, key=str)
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[GroupMember.user_id, GroupMember.group_id]
            )
        )
        await self.session.flush()

    async def _purge_manual_members(self, group_id: UUID) -> None:
        """Drop hand-added rows from a group the IdP now owns.

        Each removal is audited on its own: this revokes access an admin
        granted by hand, so one event per group is not enough to answer who
        lost what.
        """
        removed = (
            await self.session.execute(
                delete(GroupMember)
                .where(GroupMember.group_id == group_id)
                .returning(GroupMember.user_id)
            )
        ).scalars()
        audit = AuditService(self.session, self.role)
        for user_id in removed:
            await audit.create_event(
                resource_type="rbac_group_member",
                action="delete",
                resource_id=group_id,
                data={"user_id": str(user_id), "reason": "idp_mapping_created"},
            )
        await self.session.flush()
