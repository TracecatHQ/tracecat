"""User provisioning from the identity provider (EE).

Provisioning is a create-or-link operation, never a create-only one. Providers
retry, and Okta queries before creating but races itself; Entra handles a 409
badly. So a POST for an email that already has an account links that account
into this organization instead of failing.

Account creation delegates to ``UserManager.provision_user_by_email``, the established
create-or-link primitive: it validates the email, links by email, and generates
and hashes a random password for a new user because ``hashed_password`` is
NOT NULL. Reimplementing it here would fork that behaviour.

This lives beside ``SCIMService`` rather than inside it because the projection
service is a pure function of the shadow tables, while provisioning writes the
user, the linkage, and organization membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi_users.exceptions import InvalidPasswordException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracecat.audit.logger import audit_log
from tracecat.auth.users import (
    InvalidEmailException,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import ensure_member, lock_role_changes
from tracecat.db.models import (
    ExternalUser,
    Invitation,
    ScimConnection,
    User,
)
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.service import BaseOrgService
from tracecat_ee.scim.service import SCIMService


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    """A user after provisioning, and whether this call created the account."""

    user: User
    external_user: ExternalUser
    created: bool

    @property
    def user_id(self) -> UUID:
        return self.user.id


class ScimProvisioningService(BaseOrgService):
    """Creates or links provider-pushed users into this organization."""

    service_name = "scim_provisioning"

    @audit_log(
        resource_type="scim_user",
        action="create",
        resource_id_attr="user_id",
    )
    async def provision_user(
        self, *, external_id: str, email: str, active: bool = True
    ) -> ProvisionedUser:
        """Create or link the provider's user, then admit them to this org.

        Linking rather than rejecting on a duplicate is deliberate: a repeated
        POST for a known email is the provider retrying, not a conflict.

        Args:
            external_id: The provider's stable identifier for the user.
            email: The user's ``userName``, which Tracecat stores as the email.
            active: Whether the provider considers the user active. A user
                pushed inactive is linked but not admitted.

        Returns:
            The provisioned user and whether the account was newly created.

        Raises:
            TracecatValidationError: The email is not a usable address.
        """
        normalized = _normalize_email(email)
        existing = await self._user_by_email(normalized)

        if existing is None:
            try:
                user = await self._create_user(normalized)
                created = True
            except IntegrityError:
                # Another request may have committed the same global email.
                await self.session.rollback()
                winner = await self._user_by_email(normalized)
                if winner is None:
                    raise
                user = winner
                created = False
        else:
            user = existing
            created = False

        # Account creation may commit; acquire the directory lock afterwards.
        await lock_role_changes(self.session, self.organization_id)
        external_user = await self._link_external_user(
            user_id=user.id, external_id=external_id, active=active
        )

        # A pending connection collects the directory without granting anything,
        # so an admin can review what arrived before anyone is admitted.
        connection_active = await self._connection_is_active()
        if not active and connection_active:
            await SCIMService(self.session, self.role).deprovision_user(user.id)
            await self.session.refresh(external_user)
        elif active and connection_active:
            # A live invitation carries its own role, possibly above what SCIM
            # grants, so accepting it later would escalate. It is revoked here.
            await self._revoke_pending_invitation(normalized)
            await self._grant_org_membership(user.id)

        await self.session.flush()
        return ProvisionedUser(user=user, external_user=external_user, created=created)

    async def update_external_id(
        self, external_user: ExternalUser, external_id: str | None
    ) -> None:
        """Update the provider identifier while preserving the account linkage."""
        if external_id is None or external_id == external_user.external_id:
            return
        await lock_role_changes(self.session, self.organization_id)
        duplicate = await self.session.scalar(
            select(ExternalUser.id).where(
                ExternalUser.organization_id == self.organization_id,
                ExternalUser.external_id == external_id,
                ExternalUser.id != external_user.id,
            )
        )
        if duplicate is not None:
            raise TracecatConflictError("An external user already uses this externalId")
        external_user.external_id = external_id
        await self.session.flush()

    async def _connection_is_active(self) -> bool:
        """Whether this organization's connection has been activated."""
        stmt = select(ScimConnection.status).where(
            ScimConnection.organization_id == self.organization_id
        )
        status = (await self.session.execute(stmt)).scalar_one_or_none()
        return status == ScimConnectionStatus.ACTIVE

    async def _create_user(self, email: str) -> User:
        """Create the account through the shared create-or-link primitive."""
        async with get_user_db_context(self.session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                try:
                    return await user_manager.provision_user_by_email(
                        email=email,
                        organization_id=self.organization_id,
                        associate_by_email=True,
                        is_verified_by_default=True,
                        allow_auto_provisioning=True,
                    )
                except (InvalidEmailException, InvalidPasswordException) as e:
                    raise TracecatValidationError(
                        "userName is not a valid email address"
                    ) from e

    async def _user_by_email(self, email: str) -> User | None:
        """Find an account by email, case-insensitively."""
        stmt = select(User).where(func.lower(User.email) == email)
        return (await self.session.execute(stmt)).scalars().first()

    async def _link_external_user(
        self, *, user_id: UUID, external_id: str, active: bool
    ) -> ExternalUser:
        """Record that this organization's provider owns the user.

        The linkage is per-tenant, so re-pushing an existing user refreshes the
        provider's identifier rather than creating a second row.
        """
        stmt = (
            pg_insert(ExternalUser)
            .values(
                organization_id=self.organization_id,
                user_id=user_id,
                external_id=external_id,
                active=active,
            )
            .on_conflict_do_update(
                index_elements=[ExternalUser.organization_id, ExternalUser.user_id],
                set_={"external_id": external_id, "active": active},
            )
            .returning(ExternalUser)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def _grant_org_membership(self, user_id: UUID) -> None:
        """Admit the user; membership itself supplies the baseline scopes."""
        await ensure_member(self.session, self.organization_id, user_id)

    async def _revoke_pending_invitation(self, email: str) -> None:
        """Revoke any live invitation for the email in this organization.

        Written directly rather than through ``OrgService.revoke_invitation``:
        that method requires ``org:member:invite``, which the SCIM connection
        deliberately does not hold.
        """
        await self.session.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == self.organization_id,
                func.lower(Invitation.email) == email,
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.REVOKED)
        )


def _normalize_email(email: str) -> str:
    """Lowercase and strip an address, rejecting anything unusable."""
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise TracecatValidationError("userName is not a valid email address")
    return normalized
