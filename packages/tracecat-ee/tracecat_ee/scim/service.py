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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    and_,
    delete,
    func,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.authz.controls import require_scope
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import lock_role_changes
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
from tracecat_ee.scim.credentials import SCIM_ROLE_SCOPES
from tracecat_ee.scim.schemas import (
    ExternalGroupMappingCreate,
    ExternalGroupMappingRead,
    ExternalGroupRead,
    ScimActivationReviewRead,
    ScimDirectoryGroupCounts,
    ScimDirectorySummaryRead,
    ScimDirectoryUserCounts,
    ScimGroupTransitionRead,
    ScimReviewPeople,
    ScimReviewPerson,
)

# People listed per outcome unless the full review is asked for.
REVIEW_PREVIEW_LIMIT = 10

ScimChangeKind = Literal["gain", "lose", "to_idp", "to_manual"]


@dataclass(frozen=True, slots=True)
class _GroupTransition:
    """Every membership change proposed mappings make to one Tracecat group."""

    group_id: UUID
    group_name: str
    added_sources: list[str]
    removed_sources: list[str]
    changes: list[tuple[UUID, ScimChangeKind]]
    # The first mapping hands a manual group's membership to the IdP.
    takes_over: bool


class SCIMService(BaseOrgService):
    """Syncs external directory state into Tracecat groups and membership."""

    service_name = "scim"

    # =========================================================================
    # Read-side operations for the admin mapping API
    # =========================================================================

    @require_scope("org:scim:manage")
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

    @require_scope("org:scim:manage")
    async def get_directory_summary(self) -> ScimDirectorySummaryRead:
        """Count the users and groups the provider has pushed.

        Returns:
            User totals split by active flag, and group totals with the number
            no mapping reads.
        """
        users_total, users_active = (
            await self.session.execute(
                select(func.count(), func.count().filter(ExternalUser.active)).where(
                    ExternalUser.organization_id == self.organization_id
                )
            )
        ).one()
        is_mapped = (
            select(ExternalGroupMapping.id)
            .where(ExternalGroupMapping.external_group_id == ExternalGroup.id)
            .exists()
        )
        groups_total, groups_unmapped = (
            await self.session.execute(
                select(func.count(), func.count().filter(~is_mapped)).where(
                    ExternalGroup.organization_id == self.organization_id
                )
            )
        ).one()
        return ScimDirectorySummaryRead(
            users=ScimDirectoryUserCounts(
                total=users_total,
                active=users_active,
                inactive=users_total - users_active,
            ),
            groups=ScimDirectoryGroupCounts(
                total=groups_total, unmapped=groups_unmapped
            ),
        )

    @require_scope("org:scim:manage")
    async def get_mapping(self, mapping_id: UUID) -> ExternalGroupMappingRead:
        """Read one mapping with both sides joined in.

        Args:
            mapping_id: The mapping to read.

        Returns:
            The mapping and the display detail for both of its sides.

        Raises:
            TracecatNotFoundError: The mapping is not in this organization.
        """
        result = await self._mapping_page(
            ExternalGroupMapping.id == mapping_id, page=PageParams(limit=1)
        )
        if not result.items:
            raise TracecatNotFoundError("External group mapping not found")
        return result.items[0]

    @require_scope("org:scim:manage")
    async def list_mappings(
        self, *, page: PageParams
    ) -> Page[ExternalGroupMappingRead]:
        """List this organization's mappings with both sides joined in.

        Returns:
            A bounded page, ordered by external name, Tracecat name, and ID.
        """
        return await self._mapping_page(page=page)

    async def _mapping_page(
        self, *criteria: ColumnElement[bool], page: PageParams
    ) -> Page[ExternalGroupMappingRead]:
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
        )
        return await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(
                ExternalGroup.display_name.asc(),
                Group.name.asc(),
                ExternalGroupMapping.id.asc(),
            ),
            row_factory=lambda row: ExternalGroupMappingRead.model_validate(
                dict(
                    zip(
                        (
                            "id",
                            "external_group_id",
                            "external_group_external_id",
                            "external_group_display_name",
                            "group_id",
                            "group_name",
                        ),
                        row,
                        strict=True,
                    )
                )
            ),
        )

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
                set_={"display_name": display_name, "updated_at": func.now()},
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
        group = await self._get_external_group(external_group_id, for_update=True)
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
        # Membership changes also modify the SCIM group resource's metadata.
        group.updated_at = func.now()
        await self.session.flush()
        await self.session.refresh(group, attribute_names=["updated_at"])

    @require_scope("org:scim:manage")
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
        await self._purge_manual_members({group_id})
        await self.session.flush()
        return mapping

    @require_scope("org:scim:manage")
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
            await self._freeze_idp_members_as_manual({group_id})
        await self.session.delete(mapping)
        await self.session.flush()

    @require_scope("org:scim:manage")
    @audit_log(resource_type="scim_group_mapping", action="update")
    async def apply_mapping_changes(
        self,
        *,
        create: Sequence[ExternalGroupMappingCreate],
        delete: Sequence[UUID],
    ) -> None:
        """Remove then add mappings in the caller's transaction.

        A group that loses every source keeps its full IdP membership as manual
        rows first, so the result does not depend on removal order. Removals run
        before additions, and an addition purges a group's manual rows. The work
        is set-based, so the org lock is held for a fixed number of queries.

        Args:
            create: Mappings to add.
            delete: Mapping IDs to remove.

        Raises:
            TracecatNotFoundError: A mapping or either side of one is not in
                this organization.
            TracecatConflictError: The connection is not active.
        """
        await lock_role_changes(self.session, self.organization_id)
        await self._change_mappings(additions=create, removals=delete)

    async def _change_mappings(
        self,
        *,
        additions: Sequence[ExternalGroupMappingCreate],
        removals: Sequence[UUID],
    ) -> None:
        """Remove then add mappings, auditing each one. Callers hold the org lock."""
        removal_ids = set(removals)
        removed: Sequence[tuple[UUID, UUID]] = (
            (
                await self.session.execute(
                    select(
                        ExternalGroupMapping.id, ExternalGroupMapping.group_id
                    ).where(
                        ExternalGroupMapping.id.in_(removal_ids),
                        ExternalGroupMapping.organization_id == self.organization_id,
                    )
                )
            )
            .tuples()
            .all()
            if removal_ids
            else []
        )
        if len(removed) != len(removal_ids):
            raise TracecatNotFoundError("External group mapping not found")
        pairs = {(m.external_group_id, m.group_id) for m in additions}
        external_group_ids = {external_group_id for external_group_id, _ in pairs}
        if external_group_ids:
            found = set(
                await self.session.scalars(
                    select(ExternalGroup.id).where(
                        ExternalGroup.id.in_(external_group_ids),
                        ExternalGroup.organization_id == self.organization_id,
                    )
                )
            )
            if found != external_group_ids:
                raise TracecatNotFoundError("External group not found")
        gaining = {group_id for _, group_id in pairs}
        await self._lock_groups({group_id for _, group_id in removed} | gaining)
        if pairs and not await self._connection_is_active():
            raise TracecatConflictError("Activate SCIM before creating group mappings")

        await self._freeze_idp_members_as_manual(
            await self._groups_losing_every_source(removed, gaining)
        )
        audit = AuditService(self.session, self.role)
        if removed:
            await self.session.execute(
                delete(ExternalGroupMapping).where(
                    ExternalGroupMapping.id.in_(removal_ids)
                )
            )
            for mapping_id, _ in sorted(removed, key=str):
                await audit.create_event(
                    resource_type="scim_group_mapping",
                    action="delete",
                    resource_id=mapping_id,
                )
        if pairs:
            ordered = sorted(pairs, key=str)
            await self.session.execute(
                pg_insert(ExternalGroupMapping)
                .values(
                    [
                        {
                            "organization_id": self.organization_id,
                            "external_group_id": external_group_id,
                            "group_id": group_id,
                        }
                        for external_group_id, group_id in ordered
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ExternalGroupMapping.external_group_id,
                        ExternalGroupMapping.group_id,
                    ]
                )
            )
            # An existing pair is audited too, matching create_mapping.
            created = await self.session.scalars(
                select(ExternalGroupMapping.id)
                .where(
                    tuple_(
                        ExternalGroupMapping.external_group_id,
                        ExternalGroupMapping.group_id,
                    ).in_(ordered),
                    ExternalGroupMapping.organization_id == self.organization_id,
                )
                .order_by(ExternalGroupMapping.id)
            )
            for mapping_id in created:
                await audit.create_event(
                    resource_type="scim_group_mapping",
                    action="create",
                    resource_id=mapping_id,
                )
            await self._purge_manual_members(gaining)
        await self.session.flush()

    @require_scope("org:scim:manage")
    @audit_log(resource_type="scim_connection", action="revoke")
    async def disconnect(self) -> None:
        """Remove every mapping, revoke the token, and disable the connection.

        One transaction. Removing a group's last mapping keeps its IdP members
        as manual rows, so nobody loses access as the directory detaches. The
        pushed users and groups are then deleted: a reconnected provider starts
        from an empty directory instead of re-admitting stale users.

        Raises:
            TracecatNotFoundError: No connection exists.
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

        mapping_ids = list(
            (
                await self.session.execute(
                    select(ExternalGroupMapping.id)
                    .where(ExternalGroupMapping.organization_id == self.organization_id)
                    .order_by(ExternalGroupMapping.id)
                )
            ).scalars()
        )
        await self._change_mappings(additions=[], removals=mapping_ids)
        # Group membership rows cascade from both sides.
        await self.session.execute(
            delete(ExternalGroup).where(
                ExternalGroup.organization_id == self.organization_id
            )
        )
        await self.session.execute(
            delete(ExternalUser).where(
                ExternalUser.organization_id == self.organization_id
            )
        )

        connection.status = ScimConnectionStatus.DISABLED
        connection.revoked_at = connection.revoked_at or datetime.now(UTC)
        self.session.add(connection)
        await self.session.commit()

    # =========================================================================
    # Activation review
    # =========================================================================

    @require_scope("org:scim:manage")
    async def review_activation(
        self,
        proposed: Sequence[ExternalGroupMappingCreate],
        delete: Sequence[UUID] = (),
        *,
        full: bool = False,
    ) -> ScimActivationReviewRead:
        """Report who activating, or applying mapping changes, would affect.

        A plain read: nothing about the returned plan is stored, so a stale
        review can only be acted on by re-running the activation itself.

        Args:
            proposed: The mappings the admin intends to install.
            delete: Mapping IDs the admin intends to remove.
            full: List every person instead of a preview per outcome.

        Returns:
            Counted people per outcome and a count summary per touched group.

        Raises:
            TracecatNotFoundError: No connection exists, or a mapping or either
                side of one is not in this organization.
        """
        limit = None if full else REVIEW_PREVIEW_LIMIT
        status = await self.session.scalar(
            select(ScimConnection.status).where(
                ScimConnection.organization_id == self.organization_id
            )
        )
        if status is None:
            raise TracecatNotFoundError("SCIM connection not found")
        pending = status == ScimConnectionStatus.PENDING
        transitions = await self._group_transitions(
            proposed,
            delete,
            # Activation admits active pushed users before installing mappings.
            assume_admitted=pending,
        )
        # Members activation removes from the organization are listed once, there.
        leaving_ids = await self._inactive_member_ids() if pending else set()
        by_kind: dict[str, dict[UUID, list[str]]] = {
            kind: {} for kind in ("lose", "to_idp", "to_manual")
        }
        for transition in transitions:
            for user_id, kind in transition.changes:
                if kind in by_kind and user_id not in leaving_ids:
                    by_kind[kind].setdefault(user_id, []).append(transition.group_name)
        emails = await self._emails(set().union(*by_kind.values()))

        def people(groups_by_user: dict[UUID, list[str]]) -> ScimReviewPeople:
            ranked = sorted(groups_by_user, key=lambda uid: (emails.get(uid, ""), uid))
            return ScimReviewPeople(
                count=len(ranked),
                items=[
                    ScimReviewPerson(
                        user_id=user_id,
                        email=emails.get(user_id, ""),
                        groups=sorted(groups_by_user[user_id]),
                    )
                    for user_id in ranked[:limit]
                ],
            )

        joining, leaving = await self._directory_outcomes(limit)
        return ScimActivationReviewRead(
            joining=joining,
            leaving=leaving,
            losing=people(by_kind["lose"]),
            to_idp=people(by_kind["to_idp"]),
            to_manual=people(by_kind["to_manual"]),
            groups=[
                ScimGroupTransitionRead(
                    group_id=transition.group_id,
                    group_name=transition.group_name,
                    added_sources=transition.added_sources,
                    removed_sources=transition.removed_sources,
                    gained=sum(kind == "gain" for _, kind in transition.changes),
                    lost=sum(kind == "lose" for _, kind in transition.changes),
                    takes_over=transition.takes_over,
                )
                for transition in transitions
            ],
        )

    @require_scope("org:scim:manage")
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
            TracecatConflictError: The connection is not pending activation.
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
        # A stale client must not activate a disconnected or already active directory.
        if connection.status != ScimConnectionStatus.PENDING:
            raise TracecatConflictError("SCIM connection is not pending activation")

        # Status first: admission and mapping installation below read it.
        connection.status = ScimConnectionStatus.ACTIVE
        self.session.add(connection)
        await self.session.flush()

        # Activation delegates the same provisioning authority as the IdP token,
        # while retaining the administrator as the audit actor.
        provisioning_role = self.role.model_copy(update={"scopes": SCIM_ROLE_SCOPES})
        await SCIMService(self.session, provisioning_role)._admit_pushed_users()
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
            await self.deprovision_user(user_id)

        await self.admit_users()
        await self.session.flush()

    async def _inactive_member_ids(self) -> set[UUID]:
        """Members the provider marked inactive; activation removes them."""
        stmt = (
            select(ExternalUser.user_id)
            .join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == ExternalUser.user_id,
                    OrganizationMembership.organization_id
                    == ExternalUser.organization_id,
                ),
            )
            .where(
                ExternalUser.organization_id == self.organization_id,
                ExternalUser.active.is_(False),
            )
        )
        return set((await self.session.execute(stmt)).scalars())

    async def _directory_outcomes(
        self, limit: int | None
    ) -> tuple[ScimReviewPeople, ScimReviewPeople]:
        """Pushed users activation admits, and inactive members it removes."""
        is_member = OrganizationMembership.user_id.is_not(None)
        stmt = (
            select(ExternalUser.user_id, User.__table__.c.email)
            .join(User, User.__table__.c.id == ExternalUser.user_id)
            .outerjoin(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == ExternalUser.user_id,
                    OrganizationMembership.organization_id
                    == ExternalUser.organization_id,
                ),
            )
            .where(ExternalUser.organization_id == self.organization_id)
        )
        outcomes: list[ScimReviewPeople] = []
        for criterion in (
            and_(ExternalUser.active, ~is_member),
            and_(ExternalUser.active.is_(False), is_member),
        ):
            filtered = stmt.where(criterion)
            count = await self.session.scalar(
                select(func.count()).select_from(filtered.subquery())
            )
            rows = await self.session.execute(
                filtered.order_by(User.__table__.c.email, ExternalUser.user_id).limit(
                    limit
                )
            )
            outcomes.append(
                ScimReviewPeople(
                    count=count or 0,
                    items=[
                        ScimReviewPerson(user_id=user_id, email=email)
                        for user_id, email in rows.tuples()
                    ],
                )
            )
        joining, leaving = outcomes
        return joining, leaving

    async def _groups_losing_every_source(
        self, removed: Sequence[tuple[UUID, UUID]], gaining: set[UUID]
    ) -> set[UUID]:
        """Groups whose every mapping is being removed and none added."""
        candidates = {group_id for _, group_id in removed} - gaining
        if not candidates:
            return set()
        kept = set(
            await self.session.scalars(
                select(ExternalGroupMapping.group_id)
                .where(
                    ExternalGroupMapping.group_id.in_(candidates),
                    ExternalGroupMapping.organization_id == self.organization_id,
                    ExternalGroupMapping.id.not_in({m for m, _ in removed}),
                )
                .distinct()
            )
        )
        return candidates - kept

    async def _group_transitions(
        self,
        create: Sequence[ExternalGroupMappingCreate],
        delete: Sequence[UUID],
        *,
        assume_admitted: bool,
    ) -> list[_GroupTransition]:
        """Simulate ``apply_mapping_changes`` per touched Tracecat group.

        Mirrors its order: groups losing every source freeze first, removals
        run, then any addition purges the group's manual rows.
        """
        removed_rows = (
            (
                await self.session.execute(
                    select(
                        ExternalGroupMapping.id,
                        ExternalGroupMapping.group_id,
                        ExternalGroupMapping.external_group_id,
                    ).where(
                        ExternalGroupMapping.id.in_(delete),
                        ExternalGroupMapping.organization_id == self.organization_id,
                    )
                )
            )
            .tuples()
            .all()
            if delete
            else []
        )
        if len(removed_rows) != len(set(delete)):
            raise TracecatNotFoundError("External group mapping not found")
        added_ids = {mapping.external_group_id for mapping in create}
        if added_ids:
            found = set(
                await self.session.scalars(
                    select(ExternalGroup.id).where(
                        ExternalGroup.id.in_(added_ids),
                        ExternalGroup.organization_id == self.organization_id,
                    )
                )
            )
            if found != added_ids:
                raise TracecatNotFoundError("External group not found")

        removed_by_group: dict[UUID, set[UUID]] = {}
        for _, group_id, external_group_id in removed_rows:
            removed_by_group.setdefault(group_id, set()).add(external_group_id)
        added_by_group: dict[UUID, set[UUID]] = {}
        for mapping in create:
            added_by_group.setdefault(mapping.group_id, set()).add(
                mapping.external_group_id
            )
        touched = set(removed_by_group) | set(added_by_group)
        if not touched:
            return []

        # Batched across touched groups so a review's query count is constant.
        group_names = dict(
            (
                await self.session.execute(
                    select(Group.id, Group.name).where(
                        Group.id.in_(touched),
                        Group.organization_id == self.organization_id,
                    )
                )
            )
            .tuples()
            .all()
        )
        if len(group_names) != len(touched):
            raise TracecatNotFoundError("Group not found")
        sources_before_by_group: dict[UUID, set[UUID]] = {}
        for group_id, external_group_id in (
            await self.session.execute(
                select(
                    ExternalGroupMapping.group_id,
                    ExternalGroupMapping.external_group_id,
                ).where(
                    ExternalGroupMapping.group_id.in_(touched),
                    ExternalGroupMapping.organization_id == self.organization_id,
                )
            )
        ).tuples():
            sources_before_by_group.setdefault(group_id, set()).add(external_group_id)
        manual_by_group: dict[UUID, set[UUID]] = {}
        for group_id, user_id in (
            await self.session.execute(
                select(GroupMember.group_id, GroupMember.user_id).where(
                    GroupMember.group_id.in_(touched)
                )
            )
        ).tuples():
            manual_by_group.setdefault(group_id, set()).add(user_id)
        members_by_source = await self._idp_members_by_source(
            set().union(*sources_before_by_group.values(), *added_by_group.values()),
            admitted_only=not assume_admitted,
        )
        source_names = await self._external_group_names(
            set().union(*removed_by_group.values(), *added_by_group.values())
        )

        def idp_members(sources: set[UUID]) -> set[UUID]:
            return set().union(*(members_by_source.get(s, set()) for s in sources))

        transitions: list[_GroupTransition] = []
        for group_id in sorted(touched, key=str):
            removed = removed_by_group.get(group_id, set())
            added = added_by_group.get(group_id, set())
            sources_before = sources_before_by_group.get(group_id, set())
            sources_after = (sources_before - removed) | added
            manual_before = manual_by_group.get(group_id, set())
            idp_before = idp_members(sources_before)
            idp_after = idp_members(sources_after)
            manual_after = set(manual_before)
            if removed and not sources_before - removed and not added:
                manual_after |= idp_before
            if added:
                manual_after = set()

            before = manual_before | idp_before
            after = manual_after | idp_after
            changes: list[tuple[UUID, ScimChangeKind]] = []
            for user_id in before | after:
                was = "idp" if user_id in idp_before else "manual"
                now = "idp" if user_id in idp_after else "manual"
                if user_id not in before:
                    changes.append((user_id, "gain"))
                elif user_id not in after:
                    changes.append((user_id, "lose"))
                elif was != now:
                    changes.append((user_id, "to_idp" if now == "idp" else "to_manual"))
            transitions.append(
                _GroupTransition(
                    group_id=group_id,
                    group_name=group_names[group_id],
                    added_sources=sorted(source_names[e] for e in added),
                    removed_sources=sorted(source_names[e] for e in removed),
                    changes=changes,
                    takes_over=bool(added) and not sources_before,
                )
            )
        return transitions

    async def _idp_members_by_source(
        self, external_group_ids: set[UUID], *, admitted_only: bool
    ) -> dict[UUID, set[UUID]]:
        """Active users each external group lists, optionally only admitted ones."""
        if not external_group_ids:
            return {}
        stmt = (
            select(ExternalGroupMember.external_group_id, ExternalUser.user_id)
            .join(ExternalUser, ExternalUser.id == ExternalGroupMember.external_user_id)
            .where(
                ExternalGroupMember.external_group_id.in_(external_group_ids),
                ExternalUser.organization_id == self.organization_id,
                ExternalUser.active,
            )
        )
        if admitted_only:
            stmt = stmt.join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == ExternalUser.user_id,
                    OrganizationMembership.organization_id
                    == ExternalUser.organization_id,
                ),
            )
        members: dict[UUID, set[UUID]] = {}
        for external_group_id, user_id in (await self.session.execute(stmt)).tuples():
            members.setdefault(external_group_id, set()).add(user_id)
        return members

    async def _external_group_names(self, ids: set[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        rows = await self.session.execute(
            select(ExternalGroup.id, ExternalGroup.display_name).where(
                ExternalGroup.id.in_(ids),
                ExternalGroup.organization_id == self.organization_id,
            )
        )
        return dict(rows.tuples().all())

    async def _emails(self, user_ids: set[UUID]) -> dict[UUID, str]:
        if not user_ids:
            return {}
        rows = await self.session.execute(
            select(User.__table__.c.id, User.__table__.c.email).where(
                User.__table__.c.id.in_(user_ids)
            )
        )
        return dict(rows.tuples().all())

    # =========================================================================
    # Deprovisioning
    # =========================================================================

    async def deprovision_user(self, user_id: UUID) -> None:
        """Remove a user from this organization at the provider's instruction.

        ``active=false`` revokes access to this tenant only: the row and its
        group links are kept so re-activation relinks the same resource id, and
        the global ``is_active`` flag is never written. Clearing ``active``
        already drops the IdP role-path arm; ``delete_member`` then removes the
        membership row and the direct and manual paths with it.
        Before connection activation, only the staged active flag changes.

        Args:
            user_id: The user the provider has deprovisioned.

        Raises:
            TracecatAuthorizationError: The user is a superuser, or the caller
                lacks ``org:member:remove``.
            TracecatNotFoundError: The account no longer exists.
        """
        await lock_role_changes(self.session, self.organization_id)
        if not await self._connection_is_active():
            # Before activation the provider owns only the staged directory.
            await self.deactivate_external_user(user_id)
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
            await self.admit_users(external_user.user_id)
        await self.session.flush()

    async def admit_users(self, user_id: UUID | None = None) -> None:
        """Admit this org's active pushed users, or just one of them.

        Two statements regardless of cohort size, revoking any invitation that
        could outrank SCIM. Callers hold the organization role-change lock and
        own the transaction. Invitation revocation is written directly because
        the SCIM connection deliberately does not hold ``org:member:invite``.

        Args:
            user_id: Admit only this user; the whole directory when omitted.
        """
        admitted = select(ExternalUser.organization_id, ExternalUser.user_id).where(
            ExternalUser.organization_id == self.organization_id,
            ExternalUser.active,
        )
        if user_id is not None:
            admitted = admitted.where(ExternalUser.user_id == user_id)
        await self.session.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == self.organization_id,
                func.lower(Invitation.email).in_(
                    select(func.lower(User.__table__.c.email)).where(
                        User.__table__.c.id.in_(
                            admitted.with_only_columns(ExternalUser.user_id)
                        )
                    )
                ),
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.REVOKED)
        )
        await self.session.execute(
            pg_insert(OrganizationMembership)
            .from_select(["organization_id", "user_id"], admitted)
            .on_conflict_do_nothing(
                index_elements=[
                    OrganizationMembership.organization_id,
                    OrganizationMembership.user_id,
                ]
            )
        )

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
        await self._lock_groups({group_id})

    async def _lock_groups(self, group_ids: set[UUID]) -> None:
        """Lock the groups, in ID order, so concurrent reconciles serialize.

        Groups are locked before any user row, matching the order RBAC's
        ``_sync_group_memberships`` takes, so the two cannot deadlock.
        """
        if not group_ids:
            return
        locked = set(
            await self.session.scalars(
                select(Group.id)
                .where(
                    Group.id.in_(group_ids),
                    Group.organization_id == self.organization_id,
                )
                .order_by(Group.id)
                .with_for_update()
            )
        )
        if locked != group_ids:
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

    async def _freeze_idp_members_as_manual(self, group_ids: set[UUID]) -> None:
        """Copy the groups' current IdP members in as manual rows.

        Losing its last mapping would otherwise revoke every member's access at
        once; the admin keeps the membership and can edit it by hand again.
        """
        # Joined through the membership row: it is the aggregate root the
        # group_member insert below hangs off, and a user the provider pushed
        # while the connection was pending has none.
        if not group_ids:
            return
        members = (
            select(ExternalGroupMapping.group_id, ExternalUser.user_id)
            .select_from(ExternalUser)
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
                ExternalGroupMapping.group_id.in_(group_ids),
                ExternalGroupMapping.organization_id == self.organization_id,
                ExternalUser.active,
            )
            .distinct()
        )
        rows = (await self.session.execute(members)).tuples().all()
        if not rows:
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
                    for group_id, user_id in sorted(rows, key=str)
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[GroupMember.user_id, GroupMember.group_id]
            )
        )
        await self.session.flush()

    async def _purge_manual_members(self, group_ids: set[UUID]) -> None:
        """Drop hand-added rows from a group the IdP now owns.

        Each removal is audited on its own: this revokes access an admin
        granted by hand, so one event per group is not enough to answer who
        lost what.
        """
        removed = (
            (
                await self.session.execute(
                    delete(GroupMember)
                    .where(GroupMember.group_id.in_(group_ids))
                    .returning(GroupMember.group_id, GroupMember.user_id)
                )
            )
            .tuples()
            .all()
        )
        audit = AuditService(self.session, self.role)
        for group_id, user_id in sorted(removed, key=str):
            await audit.create_event(
                resource_type="rbac_group_member",
                action="delete",
                resource_id=group_id,
                data={"user_id": str(user_id), "reason": "idp_mapping_created"},
            )
        await self.session.flush()
