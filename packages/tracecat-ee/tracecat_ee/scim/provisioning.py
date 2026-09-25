"""User provisioning from the identity provider (EE).

Provisioning is a create-or-link operation, never a create-only one. Providers
retry, and Okta queries before creating but races itself; Entra handles a 409
badly. So a POST for an email that already has an account links that account
into this organization instead of failing.

The provider only acts on addresses at domains this organization owns. That is
what makes SCIM the source of truth for them: linking, admitting, and renaming
an account are all bounded by the same domain policy the SAML callback applies.

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
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracecat.audit.logger import audit_log
from tracecat.auth.domain_policy import is_domain_allowed_for_org
from tracecat.auth.users import (
    InvalidEmailException,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import lock_role_changes
from tracecat.db.models import (
    ExternalUser,
    OrganizationDomain,
    ScimConnection,
    User,
)
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.organization.domains import normalize_domain
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
            TracecatValidationError: The email is not a usable address, or is
                not at a domain this organization owns.
        """
        normalized = _normalize_email(email)
        await self._require_owned_domain(normalized)
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
            await SCIMService(self.session, self.role).admit_users(user.id)

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

    @audit_log(resource_type="scim_user", action="update", resource_id_attr="user_id")
    async def rename_user(self, *, user_id: UUID, email: str) -> None:
        """Change the account's global email to the provider's new ``userName``.

        Both addresses must be at domains this organization owns.

        Raises:
            TracecatValidationError: Either address is outside the owned domains.
            TracecatConflictError: Another account already uses the new address.
        """
        normalized = _normalize_email(email)
        user = await self.session.get_one(User, user_id)
        await self._require_owned_domain(user.email)
        await self._require_owned_domain(normalized)
        if await self._user_by_email(normalized) is not None:
            raise TracecatConflictError("Another account already uses this userName")
        user.email = normalized
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise TracecatConflictError(
                "Another account already uses this userName"
            ) from e

    async def _require_owned_domain(self, email: str) -> None:
        """Reject an address outside this organization's domain policy."""
        _, _, raw_domain = email.rpartition("@")
        try:
            domain = normalize_domain(raw_domain).normalized_domain
        except ValueError as e:
            raise TracecatValidationError(
                "userName is not a valid email address"
            ) from e
        active_domains = set(
            (
                await self.session.execute(
                    select(OrganizationDomain.normalized_domain).where(
                        OrganizationDomain.organization_id == self.organization_id,
                        OrganizationDomain.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not is_domain_allowed_for_org(
            normalized_domain=domain, active_domains=active_domains
        ):
            raise TracecatValidationError(
                "userName must be at a domain this organization owns"
            )

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

        Raises:
            TracecatConflictError: Another account in this organization already
                holds the identifier. The upsert below resolves a conflict on
                the user, so this one would surface as an integrity error.
        """
        duplicate = await self.session.scalar(
            select(ExternalUser.id).where(
                ExternalUser.organization_id == self.organization_id,
                ExternalUser.external_id == external_id,
                ExternalUser.user_id != user_id,
            )
        )
        if duplicate is not None:
            raise TracecatConflictError("An external user already uses this externalId")
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


def _normalize_email(email: str) -> str:
    """Lowercase and strip an address, rejecting anything unusable."""
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise TracecatValidationError("userName is not a valid email address")
    return normalized
