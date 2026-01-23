"""Shared registry sync logic for org and platform scopes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

import aiofiles
from sqlalchemy.orm import Mapped

from tracecat import config
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess
from tracecat.registry.sync.tarball import (
    TarballBuildError,
    TarballVenvBuildResult,
    build_builtin_registry_tarball_venv,
    build_tarball_venv_from_git,
    build_tarball_venv_from_path,
    get_tarball_venv_s3_key,
    upload_tarball_venv,
)
from tracecat.registry.versions.schemas import (
    RegistryVersionCreate,
    RegistryVersionManifest,
)
from tracecat.service import BaseService
from tracecat.storage import blob

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.ssh import SshEnv


class RepositoryProtocol(Protocol):
    id: Mapped[uuid.UUID]
    origin: Mapped[str]
    current_version_id: Mapped[uuid.UUID | None]


class VersionProtocol(Protocol):
    id: Mapped[uuid.UUID]
    version: Mapped[str]
    tarball_uri: Mapped[str]


class VersionsServiceProtocol[VersionT: VersionProtocol](Protocol):
    def __init__(self, session: AsyncSession, role: Role | None = None) -> None: ...

    async def get_version_by_repo_and_version(
        self,
        repository_id: uuid.UUID,
        version: str,
    ) -> VersionT | None: ...

    async def create_version(
        self,
        params: RegistryVersionCreate,
        *,
        commit: bool = True,
    ) -> VersionT: ...

    async def populate_index_from_manifest(
        self,
        version: VersionT,
        *,
        commit: bool = True,
    ) -> Sequence[object]: ...


@dataclass
class ArtifactsBuildResult:
    """Result of building and uploading tarball artifacts."""

    tarball_uri: str


@dataclass
class BaseSyncResult[VersionT: VersionProtocol]:
    """Result of a registry sync operation."""

    version: VersionT
    actions: list[RegistryActionCreate]
    tarball_uri: str
    commit_sha: str | None = None

    @property
    def version_string(self) -> str:
        return str(self.version.version)

    @property
    def num_actions(self) -> int:
        return len(self.actions)


class BaseRegistrySyncService[
    RepoT: RepositoryProtocol,
    VersionT: VersionProtocol,
](BaseService):
    """Base class for registry sync operations."""

    @classmethod
    def _versions_service_cls(cls) -> type[VersionsServiceProtocol[VersionT]]:
        raise NotImplementedError

    @classmethod
    def _result_cls(cls) -> type[BaseSyncResult[VersionT]]:
        raise NotImplementedError

    @classmethod
    def _sync_error_cls(cls) -> type[Exception]:
        return Exception

    @classmethod
    def _storage_namespace(cls) -> str | None:
        return None

    async def sync_repository_v2(
        self,
        db_repo: RepoT,
        *,
        target_version: str | None = None,
        target_commit_sha: str | None = None,
        ssh_env: SshEnv | None = None,
        git_repo_package_name: str | None = None,
        commit: bool = True,
    ) -> BaseSyncResult[VersionT]:
        """Sync a repository and create a versioned snapshot."""
        origin = str(db_repo.origin)
        repo_id = db_repo.id

        self.logger.info(
            "Starting registry sync",
            repository=origin,
            target_version=target_version,
            target_commit_sha=target_commit_sha,
            sandbox_enabled=config.TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED,
        )

        if config.TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED:
            return await self._sync_via_temporal_workflow(
                db_repo=db_repo,
                target_version=target_version,
                target_commit_sha=target_commit_sha,
                ssh_env=ssh_env,
                git_repo_package_name=git_repo_package_name,
                commit=commit,
            )

        sync_result = await fetch_actions_from_subprocess(
            origin=origin,
            repository_id=repo_id,
            commit_sha=target_commit_sha,
            validate=True,
            git_repo_package_name=git_repo_package_name,
        )
        actions = sync_result.actions
        commit_sha = sync_result.commit_sha

        if not actions:
            raise self._sync_error_cls()(f"No actions found in repository {origin}")

        self.logger.info(
            "Fetched actions from repository",
            num_actions=len(actions),
            commit_sha=commit_sha,
        )

        manifest = RegistryVersionManifest.from_actions(actions)

        if target_version is None:
            target_version = self._generate_version_string(
                origin=origin, commit_sha=commit_sha
            )

        versions_service = self._get_versions_service()
        existing_version = await versions_service.get_version_by_repo_and_version(
            repository_id=repo_id,
            version=target_version,
        )
        if existing_version:
            tarball_uri = cast(str | None, existing_version.tarball_uri)
            if tarball_uri is None:
                raise self._sync_error_cls()(
                    f"Version {target_version} exists but has no tarball artifact. "
                    + "Delete the version and re-sync to create a valid version."
                )
            self.logger.info(
                "Version already exists, returning existing",
                version=target_version,
                version_id=str(existing_version.id),
            )
            return self._make_result(
                version=existing_version,
                actions=actions,
                tarball_uri=tarball_uri,
                commit_sha=commit_sha,
            )

        artifacts = await self._build_and_upload_artifacts(
            origin=origin,
            version_string=target_version,
            commit_sha=commit_sha,
            ssh_env=ssh_env,
        )

        version_create = RegistryVersionCreate(
            repository_id=repo_id,
            version=target_version,
            commit_sha=commit_sha,
            manifest=manifest,
            tarball_uri=artifacts.tarball_uri,
        )
        version = await versions_service.create_version(version_create, commit=False)

        self.logger.info(
            "Created registry version",
            version_id=str(version.id),
            version=target_version,
            tarball_uri=artifacts.tarball_uri,
        )

        # Auto-promote: set new version as current
        db_repo.current_version_id = version.id
        self.session.add(db_repo)

        _ = await versions_service.populate_index_from_manifest(version, commit=False)

        if commit:
            await self.session.commit()
            await self.session.refresh(version)
        else:
            await self.session.flush()
            await self.session.refresh(version)

        self.logger.info(
            "Registry sync completed",
            version_id=str(version.id),
            version=target_version,
            num_actions=len(actions),
            tarball_uri=artifacts.tarball_uri,
        )

        return self._make_result(
            version=version,
            actions=actions,
            tarball_uri=artifacts.tarball_uri,
            commit_sha=commit_sha,
        )

    def _get_versions_service(self) -> VersionsServiceProtocol[VersionT]:
        return self._versions_service_cls()(self.session, self.role)

    def _make_result(
        self,
        *,
        version: VersionT,
        actions: list[RegistryActionCreate],
        tarball_uri: str,
        commit_sha: str | None,
    ) -> BaseSyncResult[VersionT]:
        return self._result_cls()(
            version=version,
            actions=actions,
            tarball_uri=tarball_uri,
            commit_sha=commit_sha,
        )

    def _get_storage_namespace(self) -> str:
        """Get storage namespace for blob storage.

        Subclasses must either:
        - Override `_storage_namespace()` to return a static namespace, OR
        - Override this method to return a dynamic namespace
        """
        namespace = self._storage_namespace()
        if namespace is None:
            raise NotImplementedError(
                f"{self.__class__.__name__} must override _storage_namespace() or "
                "_get_storage_namespace() to provide a storage namespace"
            )
        return namespace

    async def _build_and_upload_artifacts(
        self,
        *,
        origin: str,
        version_string: str,
        commit_sha: str | None,
        ssh_env: SshEnv | None = None,
    ) -> ArtifactsBuildResult:
        async with aiofiles.tempfile.TemporaryDirectory(
            prefix="tracecat_tarball_"
        ) as temp_dir:
            output_dir = Path(temp_dir)
            tarball_result: TarballVenvBuildResult

            if origin == DEFAULT_REGISTRY_ORIGIN:
                tarball_result = await build_builtin_registry_tarball_venv(output_dir)

            elif origin == DEFAULT_LOCAL_REGISTRY_ORIGIN:
                local_path = Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH)
                tarball_result = await build_tarball_venv_from_path(
                    local_path, output_dir
                )

            elif origin.startswith("git+ssh://"):
                if commit_sha is None:
                    raise TarballBuildError(
                        "commit_sha is required for git repositories"
                    )

                self.logger.info(
                    "Building tarball venv for git repository",
                    origin=origin,
                )
                tarball_result = await build_tarball_venv_from_git(
                    git_url=origin,
                    commit_sha=commit_sha,
                    env=ssh_env,
                    output_dir=output_dir,
                )

            else:
                raise TarballBuildError(
                    f"Unsupported origin for artifact building: {origin}"
                )

            bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY
            await blob.ensure_bucket_exists(bucket)

            tarball_s3_key = get_tarball_venv_s3_key(
                organization_id=self._get_storage_namespace(),
                repository_origin=origin,
                version=version_string,
            )
            tarball_uri = await upload_tarball_venv(
                tarball_path=tarball_result.tarball_path,
                key=tarball_s3_key,
                bucket=bucket,
            )
            self.logger.info(
                "Tarball venv uploaded",
                tarball_uri=tarball_uri,
                compressed_size_bytes=tarball_result.compressed_size_bytes,
                uncompressed_size_bytes=tarball_result.uncompressed_size_bytes,
            )

            return ArtifactsBuildResult(tarball_uri=tarball_uri)

    def _generate_version_string(
        self,
        origin: str,
        commit_sha: str | None,
    ) -> str:
        if commit_sha:
            return commit_sha[:7]

        if origin == DEFAULT_REGISTRY_ORIGIN:
            import tracecat_registry

            return tracecat_registry.__version__

        now = datetime.now(UTC)
        return now.strftime("%Y.%m.%d.%H%M%S")

    def _get_origin_type(self, origin: str) -> Literal["builtin", "local", "git"]:
        if origin == DEFAULT_REGISTRY_ORIGIN:
            return "builtin"
        if origin == DEFAULT_LOCAL_REGISTRY_ORIGIN:
            return "local"
        if origin.startswith("git+ssh://"):
            return "git"
        raise self._sync_error_cls()(f"Unknown origin type for: {origin}")

    async def _sync_via_temporal_workflow(
        self,
        db_repo: RepoT,
        *,
        target_version: str | None = None,
        target_commit_sha: str | None = None,
        ssh_env: SshEnv | None = None,
        git_repo_package_name: str | None = None,
        commit: bool = True,
    ) -> BaseSyncResult[VersionT]:
        from temporalio.common import RetryPolicy

        from tracecat.dsl.client import get_temporal_client
        from tracecat.registry.sync.workflow import RegistrySyncWorkflow

        origin = str(db_repo.origin)
        repo_id = db_repo.id

        if ssh_env is not None:
            self.logger.debug(
                "Ignoring ssh_env for workflow sync; SSH is handled in the activity",
            )

        self.logger.info(
            "Starting sandboxed registry sync via Temporal workflow",
            repository=origin,
            repository_id=str(repo_id),
        )

        origin_type = self._get_origin_type(origin)

        ssh_key: str | None = None
        if origin_type == "git":
            from tracecat.secrets.service import SecretsService

            secrets_service = SecretsService(self.session, role=self.role)
            try:
                secret = await secrets_service.get_ssh_key(target="registry")
                ssh_key = secret.get_secret_value()
            except Exception as exc:
                self.logger.warning(
                    "Could not retrieve SSH key for git operations",
                    error=str(exc),
                )

        request = RegistrySyncRequest(
            repository_id=repo_id,
            origin=origin,
            origin_type=origin_type,
            git_url=origin if origin_type == "git" else None,
            commit_sha=target_commit_sha,
            git_repo_package_name=git_repo_package_name,
            ssh_key=ssh_key,
            validate_actions=True,
            storage_namespace=self._get_storage_namespace(),
        )

        client = await get_temporal_client()

        workflow_id = (
            f"registry-sync-{repo_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        )

        try:
            workflow_result = await client.execute_workflow(
                RegistrySyncWorkflow.run,
                request,
                id=workflow_id,
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
                execution_timeout=timedelta(minutes=20),
                retry_policy=RetryPolicy(
                    maximum_attempts=2,
                    initial_interval=timedelta(seconds=5),
                ),
            )
        except Exception as exc:
            self.logger.error(
                "Registry sync workflow failed",
                repository=origin,
                error=str(exc),
            )
            raise self._sync_error_cls()(
                f"Registry sync workflow failed: {exc}"
            ) from exc

        actions = workflow_result.actions
        tarball_uri = workflow_result.tarball_uri
        commit_sha = workflow_result.commit_sha

        if not actions:
            raise self._sync_error_cls()(f"No actions found in repository {origin}")

        self.logger.info(
            "Workflow completed, processing results",
            num_actions=len(actions),
            tarball_uri=tarball_uri,
            commit_sha=commit_sha,
        )

        manifest = RegistryVersionManifest.from_actions(actions)

        if target_version is None:
            target_version = self._generate_version_string(
                origin=origin, commit_sha=commit_sha
            )

        versions_service = self._get_versions_service()
        existing_version = await versions_service.get_version_by_repo_and_version(
            repository_id=repo_id,
            version=target_version,
        )
        if existing_version:
            existing_tarball_uri = cast(str | None, existing_version.tarball_uri)
            if existing_tarball_uri is None:
                raise self._sync_error_cls()(
                    f"Version {target_version} exists but has no tarball artifact. "
                    + "Delete the version and re-sync to create a valid version."
                )
            self.logger.info(
                "Version already exists, returning existing",
                version=target_version,
                version_id=str(existing_version.id),
            )
            return self._make_result(
                version=existing_version,
                actions=actions,
                tarball_uri=existing_tarball_uri,
                commit_sha=commit_sha,
            )

        version_create = RegistryVersionCreate(
            repository_id=repo_id,
            version=target_version,
            commit_sha=commit_sha,
            manifest=manifest,
            tarball_uri=tarball_uri,
        )
        version = await versions_service.create_version(version_create, commit=False)

        self.logger.info(
            "Created registry version",
            version_id=str(version.id),
            version=target_version,
            tarball_uri=tarball_uri,
        )

        # Auto-promote: set new version as current
        db_repo.current_version_id = version.id
        self.session.add(db_repo)

        _ = await versions_service.populate_index_from_manifest(version, commit=False)

        if commit:
            await self.session.commit()
            await self.session.refresh(version)
        else:
            await self.session.flush()
            await self.session.refresh(version)

        self.logger.info(
            "Registry sync (via workflow) completed",
            version_id=str(version.id),
            version=target_version,
            num_actions=len(actions),
            tarball_uri=tarball_uri,
        )

        return self._make_result(
            version=version,
            actions=actions,
            tarball_uri=tarball_uri,
            commit_sha=commit_sha,
        )
