"""Directory synchronization from an external identity provider.

The identity provider owns which users are in which external group; Tracecat
owns what a group grants. External groups never enter RBAC directly. An
admin-authored mapping projects their members into ``group_member`` rows marked
``scim``, which then inherit scopes from the group's existing role assignments.

The projection is a cache: it is recomputed by set reconciliation against the
shadow tables, never by applying per-event deltas, so it can be rebuilt from
scratch at any time. It is purely a function of the shadow tables and the
mapping; no global account flag is an input.

Deprovisioning is org-scoped removal, not global deactivation. ``active=false``
from one tenant's provider must not reach that user's other organizations, so it
delegates to ``OrgService.delete_member`` rather than writing
``is_active``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.authz.enums import GroupMemberSource
from tracecat.db.models import (
    ExternalGroup,
    ExternalGroupMapping,
    ExternalGroupMember,
    Group,
    GroupMember,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.organization.service import OrgService
from tracecat.service import BaseOrgService


class SCIMService(BaseOrgService):
    """Syncs external directory state into Tracecat groups and membership."""

    service_name = "scim"

    # =========================================================================
    # Reconciliation
    # =========================================================================

    async def recompute_group(self, group_id: UUID) -> None:
        """Reconcile one group's scim-sourced membership with its mappings.

        Args:
            group_id: The Tracecat group to reconcile.

        Raises:
            TracecatNotFoundError: The group does not belong to this organization.
        """
        await self._lock_group(group_id)
        await self._reconcile_group(group_id)

    async def _reconcile_group(self, group_id: UUID) -> None:
        """Reconcile a group whose lock this transaction already holds."""
        desired = await self._desired_members(group_id)
        stored = await self._stored_scim_members(group_id)

        added = desired - stored
        removed = stored - desired
        if not added and not removed:
            return

        if removed:
            await self.session.execute(
                delete(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id.in_(removed),
                    GroupMember.source == GroupMemberSource.SCIM,
                )
            )
        if added:
            # A manual row already grants the same membership and outranks the
            # projection, so it is left untouched rather than reclassified.
            await self.session.execute(
                pg_insert(GroupMember)
                .values(
                    [
                        {
                            "group_id": group_id,
                            "user_id": user_id,
                            "source": GroupMemberSource.SCIM,
                        }
                        for user_id in sorted(added, key=str)
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[GroupMember.user_id, GroupMember.group_id]
                )
            )

    async def recompute_groups(self, group_ids: Sequence[UUID]) -> None:
        """Reconcile several groups in a stable order.

        Every group lock is taken up front, before the first user lock: locking
        one group at a time would interleave group and user locks and deadlock
        two transactions touching the same groups in different orders.

        Args:
            group_ids: Groups to reconcile; duplicates are collapsed.
        """
        ordered = sorted(set(group_ids), key=str)
        for group_id in ordered:
            await self._lock_group(group_id)
        for group_id in ordered:
            await self._reconcile_group(group_id)

    async def recompute_for_external_group(self, external_group_id: UUID) -> None:
        """Reconcile every Tracecat group the external group maps into.

        Args:
            external_group_id: The synced external group that changed.
        """
        await self.recompute_groups(await self._mapped_group_ids(external_group_id))

    # =========================================================================
    # Write-side operations for the sync endpoints
    # =========================================================================

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

    async def delete_external_group(self, external_group_id: UUID) -> None:
        """Delete a synced external group and drop what it supplied.

        Args:
            external_group_id: The external group to remove.

        Raises:
            TracecatNotFoundError: The external group is not in this organization.
        """
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
        affected = await self._mapped_group_ids(external_group_id)
        await self.session.delete(external_group)
        await self.session.flush()
        await self.recompute_groups(affected)

    async def replace_external_group_members(
        self, external_group_id: UUID, user_ids: Sequence[UUID]
    ) -> None:
        """Replace an external group's member list with the provider's.

        Args:
            external_group_id: The external group being synced.
            user_ids: The complete member list as pushed by the provider.

        Raises:
            TracecatNotFoundError: The external group is not in this organization.
        """
        # Serializes concurrent replacements: without it two pushes can each
        # delete nothing and insert independently, leaving their union.
        await self._get_external_group(external_group_id, for_update=True)
        desired = set(user_ids)

        stale = delete(ExternalGroupMember).where(
            ExternalGroupMember.external_group_id == external_group_id
        )
        if desired:
            stale = stale.where(ExternalGroupMember.user_id.not_in(desired))
        await self.session.execute(stale)

        if desired:
            await self.session.execute(
                pg_insert(ExternalGroupMember)
                .values(
                    [
                        {"external_group_id": external_group_id, "user_id": user_id}
                        for user_id in sorted(desired, key=str)
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ExternalGroupMember.external_group_id,
                        ExternalGroupMember.user_id,
                    ]
                )
            )
        await self.session.flush()
        await self.recompute_for_external_group(external_group_id)

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
        await self._get_external_group(external_group_id)
        await self._lock_group(group_id)

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
        await self.session.flush()
        await self.recompute_group(group_id)
        return mapping

    async def delete_mapping(self, mapping_id: UUID) -> None:
        """Remove a mapping and drop the membership only it supplied.

        Args:
            mapping_id: The mapping to remove.

        Raises:
            TracecatNotFoundError: The mapping is not in this organization.
        """
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
        await self.session.delete(mapping)
        await self.session.flush()
        await self.recompute_group(group_id)

    # =========================================================================
    # Deprovisioning
    # =========================================================================

    async def deprovision_user(self, user_id: UUID) -> None:
        """Remove a user from this organization at the provider's instruction.

        ``active=false`` revokes access to this tenant only, so this delegates to
        ``OrgService.delete_member``: the global user record and every
        membership in other organizations survive. The global ``is_active`` flag
        is never written.

        ``delete_member`` deletes all of this organization's ``group_member``
        rows for the user, manual as well as scim-sourced. If the provider later
        re-adds them to a mapped group, the recompute restores only the scim
        rows; a manual grant revoked by a real removal stays revoked.

        Args:
            user_id: The user the provider has deprovisioned.

        Raises:
            TracecatAuthorizationError: The user is a superuser, or the caller
                lacks ``org:member:remove``.
            NoResultFound: The user is not a member of this organization.
        """
        org_service = OrgService(self.session, self.role)
        # Resolved first: forgetting the shadow rows below removes the only
        # org presence a mapped-group user has, and delete_member's own lookup
        # would then find nothing.
        member = await org_service.get_member(user_id)

        # Locked before the shadow rows go: a concurrent reconcile that already
        # read the desired set would otherwise insert the membership back after
        # this removal commits.
        for group_id in sorted(set(await self._scim_group_ids(user_id))):
            await self._lock_group(group_id)

        # Dropped first: they are what the projection reads, so leaving them
        # would let a later recompute re-add the membership.
        await self._forget_external_membership(user_id)

        # No recompute needed: delete_member removes every group_member row and
        # role assignment this organization holds for the user.
        # Commits, so it runs last: everything above is in its transaction.
        # The org guard refuses SCIM-managed members; this path is the provider
        # deprovisioning them, which is the one removal that is authoritative.
        await org_service.delete_member(user_id, allow_scim_managed=True, member=member)

    async def _scim_group_ids(self, user_id: UUID) -> list[UUID]:
        """Groups a mapping currently projects this user into."""
        stmt = (
            select(ExternalGroupMapping.group_id)
            .join(
                ExternalGroupMember,
                ExternalGroupMember.external_group_id
                == ExternalGroupMapping.external_group_id,
            )
            .where(
                ExternalGroupMember.user_id == user_id,
                ExternalGroupMapping.organization_id == self.organization_id,
            )
            .distinct()
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _forget_external_membership(self, user_id: UUID) -> None:
        """Drop the user from every external group list in this organization."""
        external_group_ids = select(ExternalGroup.id).where(
            ExternalGroup.organization_id == self.organization_id
        )
        await self.session.execute(
            delete(ExternalGroupMember).where(
                ExternalGroupMember.user_id == user_id,
                ExternalGroupMember.external_group_id.in_(external_group_ids),
            )
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

    async def _desired_members(self, group_id: UUID) -> set[UUID]:
        """Users any mapping into this group currently supplies.

        Purely a function of the shadow tables and the mapping. A deprovisioned
        user leaves the projection because org-scoped removal deleted the rows
        that supplied them, not because of an account flag.
        """
        stmt = (
            select(ExternalGroupMember.user_id)
            .join(
                ExternalGroupMapping,
                ExternalGroupMapping.external_group_id
                == ExternalGroupMember.external_group_id,
            )
            .where(
                ExternalGroupMapping.group_id == group_id,
                ExternalGroupMapping.organization_id == self.organization_id,
            )
        )
        return set((await self.session.execute(stmt)).scalars())

    async def _stored_scim_members(self, group_id: UUID) -> set[UUID]:
        stmt = select(GroupMember.user_id).where(
            GroupMember.group_id == group_id,
            GroupMember.source == GroupMemberSource.SCIM,
        )
        return set((await self.session.execute(stmt)).scalars())
