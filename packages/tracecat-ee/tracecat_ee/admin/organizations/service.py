"""Organization management service for admin control plane."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditAction
from tracecat.auth.types import AccessLevel, Role
from tracecat.db.models import (
    Organization,
    OrganizationDomain,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.organization.domains import normalize_domain
from tracecat.organization.management import (
    create_organization_with_defaults,
    delete_organization_with_cleanup,
    validate_organization_delete_confirmation,
)
from tracecat.service import BasePlatformService
from tracecat_ee.admin.organizations.schemas import (
    OrgCreate,
    OrgDomainCreate,
    OrgDomainRead,
    OrgDomainUpdate,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    OrgRegistryVersionRead,
    OrgUpdate,
)


class AdminOrgService(BasePlatformService):
    """Platform-level organization management."""

    service_name = "admin_org"

    async def list_organizations(self) -> Sequence[OrgRead]:
        """List all organizations."""
        stmt = select(Organization).order_by(Organization.created_at.desc())
        result = await self.session.execute(stmt)
        return OrgRead.list_adapter().validate_python(result.scalars().all())

    async def create_organization(self, params: OrgCreate) -> OrgRead:
        """Create a new organization with default settings and workspace."""
        org = await create_organization_with_defaults(
            self.session,
            name=params.name,
            slug=params.slug,
        )
        return OrgRead.model_validate(org)

    async def get_organization(self, org_id: uuid.UUID) -> OrgRead:
        """Get organization by ID."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        return OrgRead.model_validate(org)

    async def _require_organization(self, org_id: uuid.UUID) -> None:
        """Ensure an organization exists."""
        stmt = select(Organization.id).where(Organization.id == org_id)
        if await self.session.scalar(stmt) is None:
            raise ValueError(f"Organization {org_id} not found")

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

    async def delete_organization(
        self,
        org_id: uuid.UUID,
        *,
        confirmation: str | None,
    ) -> None:
        """Delete organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        validate_organization_delete_confirmation(org, confirmation=confirmation)
        await delete_organization_with_cleanup(
            self.session,
            organization=org,
            operator_user_id=self.role.user_id,
        )
        await self.session.commit()

    # Org Domain Methods

    async def list_org_domains(self, org_id: uuid.UUID) -> Sequence[OrgDomainRead]:
        """List assigned domains for an organization."""
        await self._require_organization(org_id)
        stmt = (
            select(OrganizationDomain)
            .where(OrganizationDomain.organization_id == org_id)
            .order_by(
                OrganizationDomain.is_primary.desc(),
                OrganizationDomain.created_at.asc(),
                OrganizationDomain.id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return OrgDomainRead.list_adapter().validate_python(result.scalars().all())

    async def create_org_domain(
        self, org_id: uuid.UUID, params: OrgDomainCreate
    ) -> OrgDomainRead:
        """Create and assign a domain to an organization."""
        await self._require_organization(org_id)
        normalized = normalize_domain(params.domain)
        await self._audit_domain_event(action="create", status=AuditEventStatus.ATTEMPT)

        stmt = select(OrganizationDomain).where(
            OrganizationDomain.organization_id == org_id,
            OrganizationDomain.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        active_domains = list(result.scalars().all())
        is_primary = params.is_primary or len(active_domains) == 0

        if is_primary:
            await self._demote_active_primaries(org_id=org_id)

        domain = OrganizationDomain(
            organization_id=org_id,
            domain=normalized.domain,
            normalized_domain=normalized.normalized_domain,
            is_primary=is_primary,
            is_active=True,
            verification_method="platform_admin",
            verified_at=None,
        )
        self.session.add(domain)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            await self._audit_domain_event(
                action="create",
                status=AuditEventStatus.FAILURE,
            )
            raise self._domain_integrity_error(
                exc, normalized.normalized_domain
            ) from exc

        await self._audit_domain_event(
            action="create",
            resource_id=domain.id,
            status=AuditEventStatus.SUCCESS,
        )
        await self.session.refresh(domain)
        return OrgDomainRead.model_validate(domain)

    async def update_org_domain(
        self, org_id: uuid.UUID, domain_id: uuid.UUID, params: OrgDomainUpdate
    ) -> OrgDomainRead:
        """Update primary/active state for an organization domain."""
        domain = await self._get_org_domain(org_id=org_id, domain_id=domain_id)
        previous_primary = domain.is_primary
        previous_active = domain.is_active
        await self._audit_domain_event(
            action="update",
            resource_id=domain.id,
            status=AuditEventStatus.ATTEMPT,
        )

        if params.is_primary is True and params.is_active is False:
            await self._audit_domain_event(
                action="update",
                resource_id=domain.id,
                status=AuditEventStatus.FAILURE,
            )
            raise ValueError("Primary domain must be active")

        next_active = (
            params.is_active if params.is_active is not None else domain.is_active
        )
        next_primary = (
            params.is_primary if params.is_primary is not None else domain.is_primary
        )
        if not next_active:
            next_primary = False

        domain.is_active = next_active
        domain.is_primary = next_primary

        if next_primary:
            domain.is_active = True
            await self._demote_active_primaries(org_id=org_id, keep_domain_id=domain.id)

        if not domain.is_active:
            domain.is_primary = False

        self.session.add(domain)
        await self._ensure_primary_invariant(org_id=org_id)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            await self._audit_domain_event(
                action="update",
                resource_id=domain.id,
                status=AuditEventStatus.FAILURE,
            )
            raise self._domain_integrity_error(exc, domain.normalized_domain) from exc

        await self.session.refresh(domain)

        if previous_primary != domain.is_primary or previous_active != domain.is_active:
            self.logger.info(
                "Organization domain updated",
                organization_id=str(org_id),
                domain_id=str(domain.id),
                is_primary=domain.is_primary,
                is_active=domain.is_active,
            )

        await self._audit_domain_event(
            action="update",
            resource_id=domain.id,
            status=AuditEventStatus.SUCCESS,
        )

        return OrgDomainRead.model_validate(domain)

    async def delete_org_domain(self, org_id: uuid.UUID, domain_id: uuid.UUID) -> None:
        """Delete an assigned organization domain."""
        domain = await self._get_org_domain(org_id=org_id, domain_id=domain_id)
        await self._audit_domain_event(
            action="delete",
            resource_id=domain.id,
            status=AuditEventStatus.ATTEMPT,
        )

        await self.session.delete(domain)
        await self.session.flush()
        await self._ensure_primary_invariant(org_id=org_id)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            await self._audit_domain_event(
                action="delete",
                resource_id=domain.id,
                status=AuditEventStatus.FAILURE,
            )
            raise ValueError("Failed to delete organization domain") from exc

        await self._audit_domain_event(
            action="delete",
            resource_id=domain.id,
            status=AuditEventStatus.SUCCESS,
        )

    async def _audit_domain_event(
        self,
        *,
        action: AuditAction,
        status: AuditEventStatus,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        """Emit audit events for organization domain operations."""
        async with AuditService.with_session(self.role, session=self.session) as svc:
            await svc.create_event(
                resource_type="organization_domain",
                action=action,
                resource_id=resource_id,
                status=status,
            )

    async def _demote_active_primaries(
        self, *, org_id: uuid.UUID, keep_domain_id: uuid.UUID | None = None
    ) -> None:
        """Demote existing active primary domains for an organization."""
        stmt = select(OrganizationDomain).where(
            OrganizationDomain.organization_id == org_id,
            OrganizationDomain.is_active.is_(True),
            OrganizationDomain.is_primary.is_(True),
        )
        if keep_domain_id is not None:
            stmt = stmt.where(OrganizationDomain.id != keep_domain_id)

        result = await self.session.execute(stmt)
        for existing_primary in result.scalars().all():
            existing_primary.is_primary = False
            self.session.add(existing_primary)

    async def _ensure_primary_invariant(self, *, org_id: uuid.UUID) -> None:
        """Ensure at most one active primary and deterministic fallback promotion."""
        stmt = (
            select(OrganizationDomain)
            .where(
                OrganizationDomain.organization_id == org_id,
                OrganizationDomain.is_active.is_(True),
            )
            .order_by(
                OrganizationDomain.created_at.asc(),
                OrganizationDomain.normalized_domain.asc(),
                OrganizationDomain.id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        active_domains = list(result.scalars().all())

        if not active_domains:
            return

        active_primary_domains = [
            domain for domain in active_domains if domain.is_primary
        ]
        selected_primary = (
            active_primary_domains[0] if active_primary_domains else active_domains[0]
        )

        for active_domain in active_domains:
            should_be_primary = active_domain.id == selected_primary.id
            if active_domain.is_primary != should_be_primary:
                active_domain.is_primary = should_be_primary
                self.session.add(active_domain)

    async def _get_org_domain(
        self, *, org_id: uuid.UUID, domain_id: uuid.UUID
    ) -> OrganizationDomain:
        """Get a domain by ID and organization."""
        stmt = select(OrganizationDomain).where(
            OrganizationDomain.id == domain_id,
            OrganizationDomain.organization_id == org_id,
        )
        result = await self.session.execute(stmt)
        domain = result.scalar_one_or_none()
        if domain is None:
            raise ValueError(f"Domain {domain_id} not found in organization {org_id}")
        return domain

    def _domain_integrity_error(
        self, error: IntegrityError, normalized_domain: str
    ) -> ValueError:
        """Translate domain-related integrity errors into user-facing messages."""
        message = (
            str(error.orig).lower() if error.orig is not None else str(error).lower()
        )
        if "ix_org_domain_normalized_domain_active_unique" in message:
            return ValueError(
                f"Domain {normalized_domain!r} is already assigned to another organization"
            )
        if "ix_org_domain_org_primary_active_unique" in message:
            return ValueError("Organization already has an active primary domain")
        return ValueError("Organization domain operation failed")

    # Org Registry Methods

    async def list_org_repositories(
        self, org_id: uuid.UUID
    ) -> Sequence[OrgRegistryRepositoryRead]:
        """List registry repositories for an organization."""
        # Verify org exists
        await self._require_organization(org_id)

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
        await self._require_organization(org_id)

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
        from tracecat.git.utils import parse_git_url
        from tracecat.registry.actions.service import RegistryActionsService
        from tracecat.registry.repositories.schemas import RegistryRepositoryUpdate
        from tracecat.registry.repositories.service import RegistryReposService
        from tracecat.registry.versions.service import RegistryVersionsService
        from tracecat.settings.service import get_setting
        from tracecat.ssh import ssh_context

        # Verify org exists
        await self._require_organization(org_id)

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

        is_git_ssh = repo.origin.startswith("git+ssh://")

        version: str | None = None
        commit_sha: str | None = None

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
                ) = await actions_service.sync_actions_from_repository(
                    repo, ssh_env=ssh_env
                )
        else:
            (
                commit_sha,
                version,
            ) = await actions_service.sync_actions_from_repository(repo)

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
        await self._require_organization(org_id)

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
