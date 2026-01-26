"""Organization management service for admin control plane."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

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
