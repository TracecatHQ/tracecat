"""Organization management service for admin control plane."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.auth.types import AccessLevel, Role
from tracecat.db.models import Organization, RegistryRepository, RegistryVersion
from tracecat.ee.compute.schemas import Tier
from tracecat.service import BaseService
from tracecat_ee.admin.organizations.schemas import (
    OrgCreate,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    OrgRegistryVersionRead,
    OrgUpdate,
    OrgUpdateTier,
)
from tracecat_ee.admin.organizations.types import TierChangeResult

if TYPE_CHECKING:
    from tracecat_ee.compute.manager import WorkerPoolManager


class AdminOrgService(BaseService):
    """Platform-level organization management."""

    service_name = "admin_org"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._worker_pool_manager: WorkerPoolManager | None = None

    @property
    def worker_pool_manager(self) -> WorkerPoolManager | None:
        """Lazy initialization of WorkerPoolManager.

        Returns None if not in a Kubernetes environment or if initialization fails.
        """
        if self._worker_pool_manager is None and config.ENTERPRISE_EDITION:
            try:
                from tracecat_ee.compute.manager import WorkerPoolManager

                self._worker_pool_manager = WorkerPoolManager(
                    in_cluster=config.TRACECAT__K8S_IN_CLUSTER
                )
            except Exception as e:
                self.logger.warning(
                    "Failed to initialize WorkerPoolManager",
                    error=str(e),
                )
        return self._worker_pool_manager

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
            tier=params.tier.value,
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

        # Provision worker pool for Enterprise tier on creation
        if params.tier == Tier.ENTERPRISE:
            await self._provision_worker_pool(str(org.id), params.tier)

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

        previous_tier = Tier(org.tier) if org.tier else Tier.STARTER

        for field, value in params.model_dump(exclude_unset=True).items():
            if field == "tier" and value is not None:
                setattr(org, field, value.value)
            else:
                setattr(org, field, value)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(f"Organization slug '{org.slug}' already exists") from e
        await self.session.refresh(org)

        # Handle tier change if tier was updated
        if params.tier is not None and params.tier != previous_tier:
            await self._handle_tier_change(str(org_id), previous_tier, params.tier)

        return OrgRead.model_validate(org)

    async def update_organization_tier(
        self, org_id: uuid.UUID, params: OrgUpdateTier
    ) -> TierChangeResult:
        """Update organization tier with worker pool management.

        This is the primary method for tier changes that handles worker pool
        provisioning/deprovisioning.
        """
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        previous_tier = Tier(org.tier) if org.tier else Tier.STARTER
        new_tier = params.tier

        if previous_tier == new_tier:
            return TierChangeResult(
                previous_tier=previous_tier,
                new_tier=new_tier,
                worker_pool_provisioned=False,
                worker_pool_deprovisioned=False,
            )

        # Update the tier in the database
        org.tier = new_tier.value
        await self.session.commit()
        await self.session.refresh(org)

        # Handle worker pool changes
        return await self._handle_tier_change(str(org_id), previous_tier, new_tier)

    async def delete_organization(self, org_id: uuid.UUID) -> None:
        """Delete organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        # Deprovision worker pool if Enterprise tier
        if Tier(org.tier) == Tier.ENTERPRISE:
            await self._deprovision_worker_pool(str(org_id))

        await self.session.delete(org)
        await self.session.commit()

    async def _handle_tier_change(
        self, org_id: str, previous_tier: Tier, new_tier: Tier
    ) -> TierChangeResult:
        """Handle worker pool provisioning/deprovisioning on tier change."""
        provisioned = False
        deprovisioned = False
        error = None

        try:
            # Enterprise -> Non-Enterprise: Deprovision dedicated pool
            if previous_tier == Tier.ENTERPRISE and new_tier != Tier.ENTERPRISE:
                await self._deprovision_worker_pool(org_id)
                deprovisioned = True
                self.logger.info(
                    "Deprovisioned worker pool for tier downgrade",
                    org_id=org_id,
                    previous_tier=previous_tier.value,
                    new_tier=new_tier.value,
                )

            # Non-Enterprise -> Enterprise: Provision dedicated pool
            elif previous_tier != Tier.ENTERPRISE and new_tier == Tier.ENTERPRISE:
                await self._provision_worker_pool(org_id, new_tier)
                provisioned = True
                self.logger.info(
                    "Provisioned worker pool for tier upgrade",
                    org_id=org_id,
                    previous_tier=previous_tier.value,
                    new_tier=new_tier.value,
                )

        except Exception as e:
            error = str(e)
            self.logger.error(
                "Failed to manage worker pool on tier change",
                org_id=org_id,
                previous_tier=previous_tier.value,
                new_tier=new_tier.value,
                error=error,
            )
            # Note: We don't rollback the tier change - the database state
            # is the source of truth. Worker pool can be reconciled later.

        return TierChangeResult(
            previous_tier=previous_tier,
            new_tier=new_tier,
            worker_pool_provisioned=provisioned,
            worker_pool_deprovisioned=deprovisioned,
            error=error,
        )

    async def _provision_worker_pool(self, org_id: str, tier: Tier) -> None:
        """Provision a worker pool for an organization."""
        if self.worker_pool_manager is None:
            self.logger.warning(
                "WorkerPoolManager not available, skipping provisioning",
                org_id=org_id,
            )
            return

        await self.worker_pool_manager.provision_worker_pool(
            org_id=org_id,
            tier=tier,
            namespace=config.TRACECAT__K8S_NAMESPACE,
        )

    async def _deprovision_worker_pool(self, org_id: str) -> None:
        """Deprovision a worker pool for an organization."""
        if self.worker_pool_manager is None:
            self.logger.warning(
                "WorkerPoolManager not available, skipping deprovisioning",
                org_id=org_id,
            )
            return

        await self.worker_pool_manager.deprovision_worker_pool(
            org_id=org_id,
            namespace=config.TRACECAT__K8S_NAMESPACE,
        )

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
