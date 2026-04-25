from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.authz.controls import require_scope
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.exceptions import RegistryError
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositorySync,
    RegistryRepositoryUpdate,
    RegistrySyncResponse,
)
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.service import BaseOrgService
from tracecat.settings.service import get_setting
from tracecat.ssh import ssh_context
from tracecat.tiers.entitlements import Entitlement, check_entitlement


class RegistryReposService(BaseOrgService):
    """Registry repository service."""

    service_name = "registry_repositories"

    @require_scope("org:registry:read")
    async def list_repositories(self) -> Sequence[RegistryRepository]:
        """Get all registry repositories for the caller's organization."""
        statement = select(RegistryRepository).where(
            RegistryRepository.organization_id == self.organization_id
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_repository(self, origin: str) -> RegistryRepository | None:
        """Get a registry by origin."""
        statement = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.origin == origin,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_repository_by_id(self, id: uuid.UUID) -> RegistryRepository:
        """Get a registry by ID."""
        statement = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.id == id,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def create_repository(
        self, params: RegistryRepositoryCreate
    ) -> RegistryRepository:
        """Create a new registry repository."""
        repository = RegistryRepository(
            organization_id=self.organization_id, origin=params.origin
        )
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions"])
        return repository

    async def update_repository(
        self, repository: RegistryRepository, params: RegistryRepositoryUpdate
    ) -> RegistryRepository:
        """Update a registry repository."""
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(repository, key, value)
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions"])
        return repository

    @require_scope("org:registry:update")
    async def sync_repository(
        self,
        repository: RegistryRepository,
        sync_params: RegistryRepositorySync | None = None,
    ) -> RegistrySyncResponse:
        """Sync an org-scoped registry repository.

        The caller must have already obtained ``repository`` through this
        service (e.g. ``get_repository_by_id`` or ``list_repositories``) so
        the org-scoped lookup has happened.

        Raises:
            EntitlementRequired: for non-default origins without the entitlement.
            RegistryError, RegistryActionValidationError,
            TracecatCredentialsNotFoundError: surfaced from the underlying sync.
        """
        # parse_git_url and RegistryActionsService are imported lazily
        # because tracecat.git.utils and tracecat.registry.repository both
        # import RegistryReposService back, which would cycle if pulled to
        # module scope.
        from tracecat.git.utils import parse_git_url
        from tracecat.registry.actions.service import RegistryActionsService

        if repository.origin != DEFAULT_REGISTRY_ORIGIN:
            await check_entitlement(
                self.session, self.role, Entitlement.CUSTOM_REGISTRY
            )

        actions_service = RegistryActionsService(self.session, self.role)
        last_synced_at = datetime.now(UTC)
        target_commit_sha = sync_params.target_commit_sha if sync_params else None
        force = sync_params.force if sync_params else False

        is_git_ssh = repository.origin.startswith("git+ssh://")
        git_repo_package_name: str | None = None
        if is_git_ssh:
            git_repo_package_name = await get_setting(
                "git_repo_package_name", role=self.role
            )

        if force and repository.current_version_id is not None:
            versions_service = RegistryVersionsService(self.session, self.role)
            current_version = await versions_service.get_version(
                repository.current_version_id
            )
            if current_version:
                self.logger.info(
                    "Force sync: deleting current version",
                    repository_id=str(repository.id),
                    version_id=str(current_version.id),
                    version=current_version.version,
                )
                await versions_service.delete_version(current_version, commit=False)
                await self.session.flush()

        if is_git_ssh:
            allowed_domains_setting = await get_setting(
                "git_allowed_domains", role=self.role
            )
            allowed_domains = allowed_domains_setting or {"github.com"}
            git_url = parse_git_url(repository.origin, allowed_domains=allowed_domains)

            async with ssh_context(
                role=self.role, git_url=git_url, session=self.session
            ) as ssh_env:
                (
                    commit_sha,
                    version,
                ) = await actions_service.sync_actions_from_repository(
                    repository,
                    target_commit_sha=target_commit_sha,
                    git_repo_package_name=git_repo_package_name,
                    ssh_env=ssh_env,
                )
        else:
            commit_sha, version = await actions_service.sync_actions_from_repository(
                repository,
                target_commit_sha=target_commit_sha,
                git_repo_package_name=git_repo_package_name,
            )

        self.logger.info(
            "Synced repository",
            repository_id=str(repository.id),
            commit_sha=commit_sha,
            version=version,
            target_commit_sha=target_commit_sha,
            last_synced_at=last_synced_at,
            force=force,
        )

        self.session.expire(repository)
        await self.update_repository(
            repository,
            RegistryRepositoryUpdate(
                last_synced_at=last_synced_at, commit_sha=commit_sha
            ),
        )
        self.logger.info("Updated repository", repository_id=str(repository.id))

        index_actions = await actions_service.list_actions_from_index_by_repository(
            repository.id
        )
        return RegistrySyncResponse(
            success=True,
            repository_id=repository.id,
            origin=repository.origin,
            version=version,
            commit_sha=commit_sha,
            actions_count=len(index_actions),
            forced=force,
        )

    async def delete_repository(self, repository: RegistryRepository) -> None:
        """Delete a registry repository."""
        if repository.current_version_id is not None:
            repository.current_version_id = None
            await self.session.flush()
        await self.session.delete(repository)
        await self.session.commit()

    async def promote_version(
        self,
        repository: RegistryRepository,
        version_id: uuid.UUID,
    ) -> RegistryRepository:
        """Promote a specific version to current.

        Guardrails:
        - Version must exist and belong to this repository
        - Version must have a valid tarball_uri

        Args:
            repository: The repository to promote a version for
            version_id: The ID of the version to promote

        Returns:
            Updated repository with new current_version_id

        Raises:
            RegistryError: If version not found, doesn't belong to repo, or missing tarball
        """
        # Fetch the version and verify it belongs to this repository
        statement = select(RegistryVersion).where(
            RegistryVersion.id == version_id,
            RegistryVersion.organization_id == self.organization_id,
        )
        result = await self.session.execute(statement)
        version = result.scalar_one_or_none()

        if version is None:
            raise RegistryError(f"Version '{version_id}' not found in organization")

        if version.repository_id != repository.id:
            raise RegistryError(
                f"Version '{version_id}' does not belong to repository '{repository.origin}'"
            )

        if not version.tarball_uri:
            raise RegistryError(
                f"Version '{version.version}' has no tarball artifact. "
                "Cannot promote a version without execution artifacts."
            )

        # Update current_version_id
        repository.current_version_id = version.id
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions"])

        self.logger.info(
            "Promoted registry version",
            repository_id=str(repository.id),
            origin=repository.origin,
            version_id=str(version.id),
            version=version.version,
        )

        return repository

    async def validate_version_deletion(
        self,
        repository: RegistryRepository,
        version: RegistryVersion,
    ) -> None:
        """Validate that a version can be deleted.

        Raises:
            RegistryError: If the version cannot be deleted
        """
        # Check 1: Cannot delete the currently promoted version
        if repository.current_version_id == version.id:
            raise RegistryError(
                "Cannot delete the currently promoted version. Promote another version first."
            )

        # Check 2: Cannot delete if published workflow definitions reference this version
        # Query WorkflowDefinition.registry_lock JSONB using containment operator
        # registry_lock structure: {"origins": {"origin": "version_string"}, "actions": {...}}
        versions_service = RegistryVersionsService(self.session, self.role)
        definitions = await versions_service.get_workflow_definitions_using_version(
            origin=repository.origin,
            version_string=version.version,
        )
        if definitions:
            workflow_ids = [str(d.workflow_id) for d in definitions[:5]]  # Show first 5
            more = f" and {len(definitions) - 5} more" if len(definitions) > 5 else ""
            raise RegistryError(
                f"Cannot delete version in use by published workflows: {', '.join(workflow_ids)}{more}"
            )
