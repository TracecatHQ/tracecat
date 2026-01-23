"""Organization management service for admin control plane."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.auth.types import AccessLevel, Role
from tracecat.db.models import (
    Organization,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.service import BaseService
from tracecat_ee.admin.organizations.schemas import (
    OrgCreate,
    OrgInvitationRead,
    OrgInviteRequest,
    OrgInviteResponse,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    OrgRegistryVersionRead,
    OrgUpdate,
)


class AdminOrgService(BaseService):
    """Platform-level organization management."""

    service_name = "admin_org"

    async def list_organizations(self) -> Sequence[OrgRead]:
        """List all organizations."""
        stmt = select(Organization).order_by(Organization.created_at.desc())
        result = await self.session.execute(stmt)
        return OrgRead.list_adapter().validate_python(result.scalars().all())

    async def create_organization(self, params: OrgCreate) -> OrgRead:
        """Create a new organization."""
        org = Organization(
            id=uuid.uuid4(),
            name=params.name,
            slug=params.slug,
        )
        self.session.add(org)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(
                f"Organization with slug '{params.slug}' already exists"
            ) from e
        await self.session.refresh(org)
        return OrgRead.model_validate(org)

    async def get_organization(self, org_id: uuid.UUID) -> OrgRead:
        """Get organization by ID."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        return OrgRead.model_validate(org)

    async def update_organization(
        self, org_id: uuid.UUID, params: OrgUpdate
    ) -> OrgRead:
        """Update organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        for field, value in params.model_dump(exclude_unset=True).items():
            setattr(org, field, value)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(f"Organization slug '{org.slug}' already exists") from e
        await self.session.refresh(org)
        return OrgRead.model_validate(org)

    async def delete_organization(self, org_id: uuid.UUID) -> None:
        """Delete organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        await self.session.delete(org)
        await self.session.commit()

    # Org Registry Methods

    async def list_org_repositories(
        self, org_id: uuid.UUID
    ) -> Sequence[OrgRegistryRepositoryRead]:
        """List registry repositories for an organization."""
        # Verify org exists
        await self.get_organization(org_id)

        stmt = select(RegistryRepository).where(
            RegistryRepository.organization_id == org_id
        )
        result = await self.session.execute(stmt)
        return [
            OrgRegistryRepositoryRead.model_validate(r) for r in result.scalars().all()
        ]

    async def list_org_repository_versions(
        self, org_id: uuid.UUID, repository_id: uuid.UUID
    ) -> Sequence[OrgRegistryVersionRead]:
        """List versions for a specific repository in an organization."""
        # Verify org exists
        await self.get_organization(org_id)

        # Verify repository exists and belongs to org
        repo_stmt = select(RegistryRepository).where(
            RegistryRepository.id == repository_id,
            RegistryRepository.organization_id == org_id,
        )
        repo_result = await self.session.execute(repo_stmt)
        repo = repo_result.scalar_one_or_none()
        if not repo:
            raise ValueError(
                f"Repository {repository_id} not found in organization {org_id}"
            )

        stmt = (
            select(RegistryVersion)
            .where(
                RegistryVersion.repository_id == repository_id,
                RegistryVersion.organization_id == org_id,
            )
            .order_by(RegistryVersion.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return [
            OrgRegistryVersionRead.model_validate(v) for v in result.scalars().all()
        ]

    async def sync_org_repository(
        self, org_id: uuid.UUID, repository_id: uuid.UUID, force: bool = False
    ) -> OrgRegistrySyncResponse:
        """Sync a registry repository for an organization."""
        from datetime import UTC, datetime

        from tracecat.feature_flags import FeatureFlag, is_feature_enabled
        from tracecat.git.utils import parse_git_url
        from tracecat.registry.actions.service import RegistryActionsService
        from tracecat.registry.repositories.schemas import RegistryRepositoryUpdate
        from tracecat.registry.repositories.service import RegistryReposService
        from tracecat.registry.versions.service import RegistryVersionsService
        from tracecat.settings.service import get_setting
        from tracecat.ssh import ssh_context

        # Verify org exists
        await self.get_organization(org_id)

        # Create a role for the org
        org_role = Role(
            type="service",
            access_level=AccessLevel.ADMIN,
            service_id="tracecat-service",
            organization_id=org_id,
        )

        # Get repository
        repos_service = RegistryReposService(self.session, org_role)
        stmt = select(RegistryRepository).where(
            RegistryRepository.id == repository_id,
            RegistryRepository.organization_id == org_id,
        )
        result = await self.session.execute(stmt)
        repo = result.scalar_one_or_none()
        if not repo:
            raise ValueError(
                f"Repository {repository_id} not found in organization {org_id}"
            )

        # Check if version already exists
        versions_service = RegistryVersionsService(self.session, org_role)
        if repo.current_version_id is not None:
            current_version = await versions_service.get_version(
                repo.current_version_id
            )
            if current_version and not force:
                # Skip sync - version already exists
                self.logger.info(
                    "Skipping sync: version already exists",
                    org_id=str(org_id),
                    repository_id=str(repository_id),
                    version=current_version.version,
                )
                # Get action count
                stmt = (
                    select(RegistryRepository)
                    .options(selectinload(RegistryRepository.actions))
                    .where(RegistryRepository.id == repository_id)
                )
                result = await self.session.execute(stmt)
                refreshed_repo = result.scalar_one()
                actions_count = len(refreshed_repo.actions)

                return OrgRegistrySyncResponse(
                    success=True,
                    repository_id=repo.id,
                    origin=repo.origin,
                    version=current_version.version,
                    commit_sha=current_version.commit_sha,
                    actions_count=actions_count,
                    forced=False,
                    skipped=True,
                    message=f"Version {current_version.version} already exists. Use --force to re-sync.",
                )
            elif current_version and force:
                # Force sync: delete current version
                self.logger.info(
                    "Force sync: deleting current version",
                    org_id=str(org_id),
                    repository_id=str(repository_id),
                    version_id=str(current_version.id),
                    version=current_version.version,
                )
                await versions_service.delete_version(current_version, commit=False)
                await self.session.flush()

        actions_service = RegistryActionsService(self.session, org_role)
        last_synced_at = datetime.now(UTC)

        # Check if v2 sync is enabled
        use_v2_sync = is_feature_enabled(FeatureFlag.REGISTRY_SYNC_V2)
        is_git_ssh = repo.origin.startswith("git+ssh://")

        version: str | None = None
        commit_sha: str | None = None

        if use_v2_sync:
            if is_git_ssh:
                allowed_domains_setting = await get_setting(
                    "git_allowed_domains", role=org_role
                )
                allowed_domains = allowed_domains_setting or {"github.com"}
                git_url = parse_git_url(repo.origin, allowed_domains=allowed_domains)

                async with ssh_context(
                    role=org_role, git_url=git_url, session=self.session
                ) as ssh_env:
                    (
                        commit_sha,
                        version,
                    ) = await actions_service.sync_actions_from_repository_v2(
                        repo, ssh_env=ssh_env
                    )
            else:
                (
                    commit_sha,
                    version,
                ) = await actions_service.sync_actions_from_repository_v2(repo)
        else:
            commit_sha = await actions_service.sync_actions_from_repository(repo)

        # Update repository
        self.session.expire(repo)
        await repos_service.update_repository(
            repo,
            RegistryRepositoryUpdate(
                last_synced_at=last_synced_at, commit_sha=commit_sha
            ),
        )

        # Get action count
        stmt = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(RegistryRepository.id == repository_id)
        )
        result = await self.session.execute(stmt)
        refreshed_repo = result.scalar_one()
        actions_count = len(refreshed_repo.actions)

        return OrgRegistrySyncResponse(
            success=True,
            repository_id=repo.id,
            origin=repo.origin,
            version=version,
            commit_sha=commit_sha,
            actions_count=actions_count,
            forced=force,
        )

    async def promote_org_repository_version(
        self, org_id: uuid.UUID, repository_id: uuid.UUID, version_id: uuid.UUID
    ) -> OrgRegistryVersionPromoteResponse:
        """Promote a registry version to be the current version for an org repository."""
        # Verify org exists
        await self.get_organization(org_id)

        # Verify repository exists and belongs to org
        repo_stmt = select(RegistryRepository).where(
            RegistryRepository.id == repository_id,
            RegistryRepository.organization_id == org_id,
        )
        repo_result = await self.session.execute(repo_stmt)
        repo = repo_result.scalar_one_or_none()
        if not repo:
            raise ValueError(
                f"Repository {repository_id} not found in organization {org_id}"
            )

        # Verify version exists and belongs to repository
        version_stmt = select(RegistryVersion).where(
            RegistryVersion.id == version_id,
            RegistryVersion.repository_id == repository_id,
            RegistryVersion.organization_id == org_id,
        )
        version_result = await self.session.execute(version_stmt)
        version = version_result.scalar_one_or_none()
        if not version:
            raise ValueError(
                f"Version {version_id} not found for repository {repository_id}"
            )

        # Validate version has tarball_uri
        if not version.tarball_uri:
            raise ValueError(f"Version {version_id} does not have a tarball")

        # Store previous version info
        previous_version_id = repo.current_version_id
        previous_version_str: str | None = None
        if previous_version_id:
            prev_version_stmt = select(RegistryVersion).where(
                RegistryVersion.id == previous_version_id
            )
            prev_version_result = await self.session.execute(prev_version_stmt)
            prev_version = prev_version_result.scalar_one_or_none()
            if prev_version:
                previous_version_str = prev_version.version

        # Update repository's current version
        repo.current_version_id = version_id
        self.session.add(repo)
        await self.session.commit()
        await self.session.refresh(repo)

        return OrgRegistryVersionPromoteResponse(
            repository_id=repository_id,
            origin=repo.origin,
            previous_version_id=previous_version_id,
            previous_version=previous_version_str,
            current_version_id=version_id,
            current_version=version.version,
        )

    # Invitation Methods

    async def invite_org_user(self, params: OrgInviteRequest) -> OrgInviteResponse:
        """Invite a user to an organization.

        If the organization doesn't exist, creates it first.
        Sends an invitation email with a magic link.

        Args:
            params: Invitation request with email, role, and optional org details.

        Returns:
            OrgInviteResponse with invitation details and magic link.

        Raises:
            TracecatAuthorizationError: If a pending invitation already exists.
        """
        from tracecat.organization.service import OrgService

        org_created = False

        # Determine slug to use
        slug = params.org_slug or "default"

        # Try to find existing org by slug
        stmt = select(Organization).where(Organization.slug == slug)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()

        if org is None:
            # If slug is "default" and it exists, try default-1, default-2, etc.
            if params.org_slug is None:
                counter = 1
                while True:
                    if counter == 1:
                        candidate_slug = "default"
                    else:
                        candidate_slug = f"default-{counter - 1}"

                    stmt = select(Organization).where(
                        Organization.slug == candidate_slug
                    )
                    result = await self.session.execute(stmt)
                    existing = result.scalar_one_or_none()

                    if existing is None:
                        slug = candidate_slug
                        break
                    counter += 1
                    if counter > 100:
                        raise ValueError("Too many default organizations")

            # Create the organization
            org_name = params.org_name or "Default Organization"
            org = Organization(
                id=uuid.uuid4(),
                name=org_name,
                slug=slug,
            )
            self.session.add(org)
            try:
                await self.session.flush()
            except IntegrityError as e:
                await self.session.rollback()
                raise ValueError(
                    f"Organization with slug '{slug}' already exists"
                ) from e
            org_created = True
            self.logger.info(
                "Created organization",
                org_id=str(org.id),
                org_name=org.name,
                org_slug=org.slug,
            )

        # Create a service role for the organization, preserving user_id for audit
        org_role = Role(
            type="service",
            access_level=AccessLevel.ADMIN,
            service_id="tracecat-api",
            organization_id=org.id,
            user_id=self.role.user_id if self.role else None,
        )

        # Use OrgService to create the invitation (handles duplicate check + email)
        org_service = OrgService(self.session, role=org_role)
        invitation_result = await org_service.create_invitation(
            email=params.email,
            role=params.role,
            organization_id=org.id,
        )

        # Commit the org creation if it was new
        if org_created:
            await self.session.commit()
            await self.session.refresh(org)

        return OrgInviteResponse(
            invitation_id=invitation_result.invitation.id,
            email=params.email,
            role=params.role,
            organization_id=org.id,
            organization_name=org.name,
            organization_slug=org.slug,
            org_created=org_created,
            magic_link=invitation_result.magic_link,
        )

    async def list_org_invitations(
        self, org_id: uuid.UUID
    ) -> Sequence[OrgInvitationRead]:
        """List all invitations for an organization.

        Args:
            org_id: Organization UUID.

        Returns:
            List of invitations for the organization.
        """
        from tracecat.db.models import OrganizationInvitation

        # Verify org exists
        await self.get_organization(org_id)

        stmt = (
            select(OrganizationInvitation)
            .where(OrganizationInvitation.organization_id == org_id)
            .order_by(OrganizationInvitation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return [OrgInvitationRead.model_validate(inv) for inv in result.scalars().all()]

    async def revoke_org_invitation(
        self, org_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> OrgInvitationRead:
        """Revoke an invitation.

        Args:
            org_id: Organization UUID.
            invitation_id: Invitation UUID.

        Returns:
            The revoked invitation.

        Raises:
            ValueError: If the invitation is not found or not pending.
        """
        from tracecat.db.models import OrganizationInvitation
        from tracecat.invitations.enums import InvitationStatus

        # Verify org exists
        await self.get_organization(org_id)

        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.organization_id == org_id,
        )
        result = await self.session.execute(stmt)
        invitation = result.scalar_one_or_none()

        if invitation is None:
            raise ValueError(
                f"Invitation {invitation_id} not found in organization {org_id}"
            )

        if invitation.status != InvitationStatus.PENDING:
            raise ValueError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )

        invitation.status = InvitationStatus.REVOKED
        await self.session.commit()
        await self.session.refresh(invitation)

        return OrgInvitationRead.model_validate(invitation)
