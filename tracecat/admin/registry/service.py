"""Platform-level registry sync service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.admin.registry.schemas import (
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
    RepositoryStatus,
    RepositorySyncResult,
)
from tracecat.db.models import (
    PlatformRegistryIndex,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.parse import safe_url
from tracecat.registry.actions.schemas import IndexEntry, RegistryActionRead
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
)
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.registry.versions.service import PlatformRegistryVersionsService
from tracecat.service import BasePlatformService

if TYPE_CHECKING:
    from tracecat_ee.admin.settings.service import AdminSettingsService


class AdminRegistryService(BasePlatformService):
    """Platform-level registry management."""

    service_name: ClassVar[str] = "admin_registry"

    async def list_repositories(self) -> list[RegistryRepositoryReadMinimal]:
        """List all platform registry repositories."""
        stmt = select(
            PlatformRegistryRepository.id,
            PlatformRegistryRepository.origin,
            PlatformRegistryRepository.last_synced_at,
            PlatformRegistryRepository.commit_sha,
            PlatformRegistryRepository.current_version_id,
        )
        result = await self.session.execute(stmt)
        rows = result.tuples().all()
        return [
            RegistryRepositoryReadMinimal(
                id=id,
                origin=origin,
                last_synced_at=last_synced_at,
                commit_sha=commit_sha,
                current_version_id=current_version_id,
            )
            for id, origin, last_synced_at, commit_sha, current_version_id in rows
        ]

    async def get_repository(self, repository_id: uuid.UUID) -> RegistryRepositoryRead:
        """Get a specific platform registry repository with its actions."""
        repos_service = PlatformRegistryReposService(self.session)
        repo = await repos_service.get_repository_by_id(repository_id)
        if repo is None:
            raise ValueError(f"Platform repository {repository_id} not found")

        actions = await self._list_platform_actions(repository_id)
        return RegistryRepositoryRead(
            id=repo.id,
            origin=repo.origin,
            last_synced_at=repo.last_synced_at,
            commit_sha=repo.commit_sha,
            current_version_id=repo.current_version_id,
            actions=actions,
        )

    async def _list_platform_actions(
        self, repository_id: uuid.UUID
    ) -> list[RegistryActionRead]:
        """List actions from platform registry index for a specific repository."""
        statement = (
            select(
                PlatformRegistryIndex.id,
                PlatformRegistryIndex.namespace,
                PlatformRegistryIndex.name,
                PlatformRegistryIndex.action_type,
                PlatformRegistryIndex.description,
                PlatformRegistryIndex.default_title,
                PlatformRegistryIndex.display_group,
                PlatformRegistryIndex.options,
                PlatformRegistryIndex.doc_url,
                PlatformRegistryIndex.author,
                PlatformRegistryIndex.deprecated,
                PlatformRegistryVersion.manifest,
                PlatformRegistryRepository.origin,
                PlatformRegistryRepository.id.label("repo_id"),
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryIndex.registry_version_id == PlatformRegistryVersion.id,
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.id == repository_id,
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
            )
        )

        result = await self.session.execute(statement)
        rows = result.all()

        actions: list[RegistryActionRead] = []
        for row in rows:
            manifest = RegistryVersionManifest.model_validate(row.manifest)
            action_name = f"{row.namespace}.{row.name}"
            manifest_action = manifest.actions.get(action_name)
            if manifest_action:
                index_entry = IndexEntry(
                    id=row.id,
                    namespace=row.namespace,
                    name=row.name,
                    action_type=row.action_type,
                    description=row.description,
                    default_title=row.default_title,
                    display_group=row.display_group,
                    options=row.options or {},
                    doc_url=row.doc_url,
                    author=row.author,
                    deprecated=row.deprecated,
                )
                actions.append(
                    RegistryActionRead.from_index_and_manifest(
                        index_entry,
                        manifest_action,
                        row.origin,
                        row.repo_id,
                    )
                )
        return actions

    async def sync_all_repositories(self, force: bool = False) -> RegistrySyncResponse:
        """Sync all platform registry repositories."""
        repos = await self._ensure_platform_repositories()

        results: list[RepositorySyncResult] = []
        for repo in repos:
            sync_result = await self._sync_repository(repo, force=force)
            results.append(sync_result)

        return RegistrySyncResponse(
            success=all(r.success for r in results),
            synced_at=datetime.now(UTC),
            repositories=results,
        )

    async def sync_repository(
        self, repository_id: uuid.UUID, force: bool = False
    ) -> RegistrySyncResponse:
        """Sync a specific platform registry repository."""
        stmt = select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.id == repository_id
        )
        result = await self.session.execute(stmt)
        repo = result.scalar_one_or_none()

        if not repo:
            raise ValueError(f"Platform repository {repository_id} not found")

        sync_result = await self._sync_repository(repo, force=force)
        return RegistrySyncResponse(
            success=sync_result.success,
            synced_at=datetime.now(UTC),
            repositories=[sync_result],
        )

    async def _ensure_platform_repositories(self) -> list[PlatformRegistryRepository]:
        """Ensure default platform repositories exist for sync operations."""
        stmt = select(PlatformRegistryRepository)
        result = await self.session.execute(stmt)
        repos = list(result.scalars().all())
        origins = {repo.origin for repo in repos}
        created = False

        if DEFAULT_REGISTRY_ORIGIN not in origins:
            self.session.add(PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN))
            created = True

        if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            if DEFAULT_LOCAL_REGISTRY_ORIGIN not in origins:
                self.session.add(
                    PlatformRegistryRepository(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
                )
                created = True

        # Custom git repo URL is an EE feature - conditionally import
        custom_git_url = await self._get_custom_git_repo_url()
        if custom_git_url:
            cleaned_url = safe_url(custom_git_url)
            if cleaned_url not in origins:
                self.session.add(PlatformRegistryRepository(origin=cleaned_url))
                created = True

        if created:
            try:
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
            stmt = select(PlatformRegistryRepository)
            result = await self.session.execute(stmt)
            repos = list(result.scalars().all())

        return repos

    async def _get_custom_git_repo_url(self) -> str | None:
        """Get custom git repo URL from EE settings (if available)."""
        try:
            from tracecat_ee.admin.settings.service import (
                AdminSettingsService as EEAdminSettingsService,
            )

            settings_service: AdminSettingsService = EEAdminSettingsService(
                self.session, role=self.role
            )
            settings = await settings_service.get_registry_settings()
            return settings.git_repo_url
        except ImportError:
            # EE not installed, skip custom git repo URL
            return None
        except Exception:
            # Any other error, skip custom git repo URL
            self.logger.debug("Failed to get custom git repo URL from EE settings")
            return None

    async def _sync_repository(
        self, repo: PlatformRegistryRepository, force: bool = False
    ) -> RepositorySyncResult:
        """Internal: Execute sync for a repository."""
        sync_service = PlatformRegistrySyncService(self.session, self.role)
        last_synced_at = datetime.now(UTC)

        # If force=True, delete the current version before syncing
        if force:
            await self._delete_current_version_if_exists(repo)

        try:
            if repo.origin.startswith("git+ssh://"):
                # NOTE: Platform git+ssh sync is not yet supported.
                # Platform registry only supports origin=tracecat-registry.
                raise ValueError(
                    f"git+ssh origins are not supported for platform registry sync. "
                    f"Origin: {repo.origin}"
                )
            else:
                sync_result = await sync_service.sync_repository_v2(
                    db_repo=repo,
                    commit=False,
                )

            repo.commit_sha = sync_result.commit_sha
            repo.last_synced_at = last_synced_at
            self.session.add(repo)
            await self.session.commit()
            await self.session.refresh(repo)

            return RepositorySyncResult(
                repository_id=repo.id,
                repository_name=repo.origin,
                success=True,
                error=None,
                version=sync_result.version_string,
                actions_count=sync_result.num_actions,
            )
        except Exception as exc:
            await self.session.rollback()
            self.logger.exception(
                "Failed to sync repository",
                repository_id=str(repo.id),
                origin=repo.origin,
            )
            return RepositorySyncResult(
                repository_id=repo.id,
                repository_name=repo.origin,
                success=False,
                error=f"Failed to sync repository: {type(exc).__name__}",
                version=None,
                actions_count=None,
            )

    async def _delete_current_version_if_exists(
        self, repo: PlatformRegistryRepository
    ) -> None:
        """Delete the current version to allow force re-sync.

        This deletes the PlatformRegistryVersion record, which also cascades
        to delete associated PlatformRegistryIndex entries.
        """
        if repo.current_version_id is None:
            return

        versions_service = PlatformRegistryVersionsService(self.session, self.role)
        version = await versions_service.get_version(repo.current_version_id)

        if version:
            self.logger.info(
                "Force sync: deleting current version",
                repository_id=str(repo.id),
                version=version.version,
                version_id=str(version.id),
            )
            # CASCADE deletes PlatformRegistryIndex entries
            # FK ondelete="SET NULL" clears repo.current_version_id
            await versions_service.delete_version(version, commit=False)
            await self.session.flush()

    async def get_status(self) -> RegistryStatusResponse:
        """Get registry health and sync status."""
        stmt = select(PlatformRegistryRepository)
        result = await self.session.execute(stmt)
        repos = list(result.scalars().all())

        last_sync = max(
            (r.last_synced_at for r in repos if r.last_synced_at), default=None
        )

        return RegistryStatusResponse(
            total_repositories=len(repos),
            last_sync_at=last_sync,
            repositories=[
                RepositoryStatus(
                    id=r.id,
                    name=r.origin,
                    origin=r.origin,
                    last_synced_at=r.last_synced_at,
                    commit_sha=r.commit_sha,
                    current_version_id=r.current_version_id,
                )
                for r in repos
            ],
        )

    async def list_versions(
        self,
        repository_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[RegistryVersionRead]:
        """List registry versions."""
        stmt = select(PlatformRegistryVersion)
        if repository_id:
            stmt = stmt.where(PlatformRegistryVersion.repository_id == repository_id)
        stmt = stmt.order_by(
            PlatformRegistryVersion.created_at.desc(),
            PlatformRegistryVersion.id.desc(),
        ).limit(limit)

        result = await self.session.execute(stmt)
        return [RegistryVersionRead.model_validate(v) for v in result.scalars().all()]

    async def promote_version(
        self,
        repository_id: uuid.UUID,
        version_id: uuid.UUID,
    ) -> RegistryVersionPromoteResponse:
        """Promote a registry version to be the current version for a repository."""
        # Fetch repository
        repo_stmt = select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.id == repository_id
        )
        repo_result = await self.session.execute(repo_stmt)
        repo = repo_result.scalar_one_or_none()

        if not repo:
            raise ValueError(f"Repository {repository_id} not found")

        # Fetch version
        version_stmt = select(PlatformRegistryVersion).where(
            PlatformRegistryVersion.id == version_id
        )
        version_result = await self.session.execute(version_stmt)
        version = version_result.scalar_one_or_none()

        if not version:
            raise ValueError(f"Version {version_id} not found")

        # Validate version belongs to repository
        if version.repository_id != repository_id:
            raise ValueError(
                f"Version {version_id} does not belong to repository {repository_id}"
            )

        # Validate version has tarball_uri
        if not version.tarball_uri:
            raise ValueError(f"Version {version_id} does not have a tarball")

        # Store previous version ID
        previous_version_id = repo.current_version_id

        # Update repository's current version
        repo.current_version_id = version_id
        self.session.add(repo)
        await self.session.commit()
        await self.session.refresh(repo)

        return RegistryVersionPromoteResponse(
            repository_id=repository_id,
            origin=repo.origin,
            previous_version_id=previous_version_id,
            current_version_id=version_id,
            version=version.version,
        )
