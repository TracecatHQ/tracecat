"""Registry sync service for v2 versioned registry flow.

This module orchestrates the full sync flow:
1. Fetch actions from subprocess or Temporal workflow
2. Build manifest from actions
3. Build tarball venv and upload to S3/MinIO
4. Create RegistryVersion record with tarball URI
5. Populate RegistryIndex entries

When TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED is True, sync operations are
delegated to the ExecutorWorker via Temporal for sandboxed execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import aiofiles

from tracecat import config
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
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
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.service import BaseService
from tracecat.storage import blob

if TYPE_CHECKING:
    from tracecat.ssh import SshEnv


class RegistrySyncError(Exception):
    """Raised when registry sync fails."""


@dataclass
class ArtifactsBuildResult:
    """Result of building and uploading tarball artifacts."""

    tarball_uri: str


@dataclass
class SyncResult:
    """Result of a registry sync operation."""

    version: RegistryVersion
    actions: list[RegistryActionCreate]
    tarball_uri: str
    commit_sha: str | None = None

    @property
    def version_string(self) -> str:
        return self.version.version

    @property
    def num_actions(self) -> int:
        return len(self.actions)


class RegistrySyncService(BaseService):
    """Service for orchestrating registry sync operations.

    This service handles the full v2 sync flow:
    - Fetching actions and building manifests
    - Creating immutable RegistryVersion records
    - Building and uploading tarball venvs to S3
    - Populating RegistryIndex for fast lookups
    """

    service_name = "registry_sync"

    async def sync_repository_v2(
        self,
        db_repo: RegistryRepository,
        *,
        target_version: str | None = None,
        target_commit_sha: str | None = None,
        ssh_env: SshEnv | None = None,
        commit: bool = True,
    ) -> SyncResult:
        """Sync a repository and create a versioned snapshot.

        This creates an immutable RegistryVersion with a tarball artifact.
        If the tarball build fails, no version is created.

        Args:
            db_repo: The repository to sync
            target_version: Version string to use (auto-generated if not provided)
            target_commit_sha: Specific commit SHA to sync (HEAD if not provided)
            ssh_env: SSH environment for git operations
            commit: Whether to commit the transaction (default: True)

        Returns:
            SyncResult with the created version and metadata

        Raises:
            RegistrySyncError: If sync fails
            TarballBuildError: If tarball building fails
        """
        self.logger.info(
            "Starting v2 registry sync",
            repository=db_repo.origin,
            target_version=target_version,
            target_commit_sha=target_commit_sha,
            sandbox_enabled=config.TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED,
        )

        # Check if sandboxed sync via Temporal workflow is enabled
        if config.TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED:
            return await self._sync_via_temporal_workflow(
                db_repo=db_repo,
                target_version=target_version,
                target_commit_sha=target_commit_sha,
                ssh_env=ssh_env,
                commit=commit,
            )

        # Step 1: Fetch actions from subprocess (original approach)
        sync_result = await fetch_actions_from_subprocess(
            origin=db_repo.origin,
            repository_id=db_repo.id,
            commit_sha=target_commit_sha,
            validate=True,
        )
        actions = sync_result.actions
        commit_sha = sync_result.commit_sha

        if not actions:
            raise RegistrySyncError(f"No actions found in repository {db_repo.origin}")

        self.logger.info(
            "Fetched actions from repository",
            num_actions=len(actions),
            commit_sha=commit_sha,
        )

        # Step 2: Build manifest from actions
        manifest = RegistryVersionManifest.from_actions(actions)

        # Step 3: Generate version string if not provided
        if target_version is None:
            target_version = self._generate_version_string(commit_sha=commit_sha)

        # Step 4: Check if version already exists
        versions_service = RegistryVersionsService(self.session, self.role)
        existing_version = await versions_service.get_version_by_repo_and_version(
            repository_id=db_repo.id,
            version=target_version,
        )
        if existing_version:
            if existing_version.tarball_uri is None:
                raise RegistrySyncError(
                    f"Version {target_version} exists but has no tarball artifact. "
                    "Delete the version and re-sync to create a valid version."
                )
            self.logger.info(
                "Version already exists, returning existing",
                version=target_version,
                version_id=str(existing_version.id),
            )
            return SyncResult(
                version=existing_version,
                actions=actions,
                tarball_uri=existing_version.tarball_uri,
                commit_sha=commit_sha,
            )

        # Step 5: Build tarball - if this fails, we don't create a version
        artifacts = await self._build_and_upload_artifacts(
            db_repo=db_repo,
            version_string=target_version,
            commit_sha=commit_sha,
            ssh_env=ssh_env,
        )

        # Step 6: Create RegistryVersion record with tarball URI
        version_create = RegistryVersionCreate(
            repository_id=db_repo.id,
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

        # Step 7: Populate RegistryIndex entries
        await versions_service.populate_index_from_manifest(version, commit=False)

        # Commit all changes if requested
        if commit:
            await self.session.commit()
            await self.session.refresh(version)
        else:
            await self.session.flush()
            await self.session.refresh(version)

        self.logger.info(
            "Registry sync v2 completed",
            version_id=str(version.id),
            version=target_version,
            num_actions=len(actions),
            tarball_uri=artifacts.tarball_uri,
        )

        return SyncResult(
            version=version,
            actions=actions,
            tarball_uri=artifacts.tarball_uri,
            commit_sha=commit_sha,
        )

    async def _build_and_upload_artifacts(
        self,
        db_repo: RegistryRepository,
        version_string: str,
        commit_sha: str | None,
        ssh_env: SshEnv | None = None,
    ) -> ArtifactsBuildResult:
        """Build tarball venv and upload to S3.

        Args:
            db_repo: The repository to build from
            version_string: Version string for the S3 key path
            commit_sha: Git commit SHA (required for git repositories)
            ssh_env: SSH environment for git operations

        Returns:
            ArtifactsBuildResult with tarball_uri.

        Raises:
            TarballBuildError: If tarball building or upload fails
        """
        async with aiofiles.tempfile.TemporaryDirectory(
            prefix="tracecat_tarball_"
        ) as temp_dir:
            output_dir = Path(temp_dir)
            tarball_result: TarballVenvBuildResult

            if db_repo.origin == DEFAULT_REGISTRY_ORIGIN:
                # Builtin registry
                tarball_result = await build_builtin_registry_tarball_venv(output_dir)

            elif db_repo.origin == DEFAULT_LOCAL_REGISTRY_ORIGIN:
                # Local registry
                local_path = Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH)
                tarball_result = await build_tarball_venv_from_path(
                    local_path, output_dir
                )

            elif db_repo.origin.startswith("git+ssh://"):
                # Git repository
                if commit_sha is None:
                    raise TarballBuildError(
                        "commit_sha is required for git repositories"
                    )

                self.logger.info(
                    "Building tarball venv for git repository",
                    origin=db_repo.origin,
                )
                tarball_result = await build_tarball_venv_from_git(
                    git_url=db_repo.origin,
                    commit_sha=commit_sha,
                    env=ssh_env,
                    output_dir=output_dir,
                )

            else:
                raise TarballBuildError(
                    f"Unsupported origin for artifact building: {db_repo.origin}"
                )

            # Ensure bucket exists
            bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY
            await blob.ensure_bucket_exists(bucket)

            # Upload tarball venv
            tarball_s3_key = get_tarball_venv_s3_key(
                organization_id=str(config.TRACECAT__DEFAULT_ORG_ID),
                repository_origin=db_repo.origin,
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
        commit_sha: str | None,
    ) -> str:
        """Generate a version string for a registry sync.

        Format depends on origin type:
        - Git: commit SHA prefix (e.g., "abc1234")
        - Builtin/Local: timestamp-based (e.g., "2024.01.15.123456")
        """
        if commit_sha:
            # Use first 7 characters of commit SHA
            return commit_sha[:7]

        # Fallback to timestamp-based version
        now = datetime.now(UTC)
        return now.strftime("%Y.%m.%d.%H%M%S")

    def _get_origin_type(self, origin: str) -> Literal["builtin", "local", "git"]:
        """Determine the origin type from the origin string."""
        if origin == DEFAULT_REGISTRY_ORIGIN:
            return "builtin"
        elif origin == DEFAULT_LOCAL_REGISTRY_ORIGIN:
            return "local"
        elif origin.startswith("git+ssh://"):
            return "git"
        else:
            raise RegistrySyncError(f"Unknown origin type for: {origin}")

    async def _sync_via_temporal_workflow(
        self,
        db_repo: RegistryRepository,
        *,
        target_version: str | None = None,
        target_commit_sha: str | None = None,
        ssh_env: SshEnv | None = None,
        commit: bool = True,
    ) -> SyncResult:
        """Sync a repository via Temporal workflow on ExecutorWorker.

        This method delegates the heavy lifting (git clone, package install,
        action discovery, tarball build) to the ExecutorWorker, then handles
        the database operations locally.

        Args:
            db_repo: The repository to sync
            target_version: Version string to use (auto-generated if not provided)
            target_commit_sha: Specific commit SHA to sync (HEAD if not provided)
            ssh_env: SSH environment for git operations
            commit: Whether to commit the transaction (default: True)

        Returns:
            SyncResult with the created version and metadata

        Raises:
            RegistrySyncError: If sync fails
        """
        from temporalio.common import RetryPolicy

        from tracecat.dsl.client import get_temporal_client
        from tracecat.registry.sync.schemas import RegistrySyncRequest
        from tracecat.registry.sync.workflow import RegistrySyncWorkflow

        self.logger.info(
            "Starting sandboxed registry sync via Temporal workflow",
            repository=db_repo.origin,
            repository_id=str(db_repo.id),
        )

        # Determine origin type
        origin_type = self._get_origin_type(db_repo.origin)

        # For git origins, retrieve the SSH key from secrets
        ssh_key: str | None = None
        if origin_type == "git":
            from tracecat.secrets.service import SecretsService

            secrets_service = SecretsService(self.session, role=self.role)
            try:
                secret = await secrets_service.get_ssh_key(target="registry")
                ssh_key = secret.get_secret_value()
            except Exception as e:
                self.logger.warning(
                    "Could not retrieve SSH key for git operations",
                    error=str(e),
                )

        # Build the workflow request
        request = RegistrySyncRequest(
            repository_id=db_repo.id,
            origin=db_repo.origin,
            origin_type=origin_type,
            git_url=db_repo.origin if origin_type == "git" else None,
            commit_sha=target_commit_sha,
            ssh_key=ssh_key,
            validate_actions=True,
        )

        # Get Temporal client
        client = await get_temporal_client()

        # Generate workflow ID
        workflow_id = (
            f"registry-sync-{db_repo.id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        )

        # Execute the workflow
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
        except Exception as e:
            self.logger.error(
                "Registry sync workflow failed",
                repository=db_repo.origin,
                error=str(e),
            )
            raise RegistrySyncError(f"Registry sync workflow failed: {e}") from e

        actions = workflow_result.actions
        tarball_uri = workflow_result.tarball_uri
        commit_sha = workflow_result.commit_sha

        if not actions:
            raise RegistrySyncError(f"No actions found in repository {db_repo.origin}")

        self.logger.info(
            "Workflow completed, processing results",
            num_actions=len(actions),
            tarball_uri=tarball_uri,
            commit_sha=commit_sha,
        )

        # Build manifest from actions
        manifest = RegistryVersionManifest.from_actions(actions)

        # Generate version string if not provided
        if target_version is None:
            target_version = self._generate_version_string(commit_sha=commit_sha)

        # Check if version already exists
        versions_service = RegistryVersionsService(self.session, self.role)
        existing_version = await versions_service.get_version_by_repo_and_version(
            repository_id=db_repo.id,
            version=target_version,
        )
        if existing_version:
            if existing_version.tarball_uri is None:
                raise RegistrySyncError(
                    f"Version {target_version} exists but has no tarball artifact. "
                    "Delete the version and re-sync to create a valid version."
                )
            self.logger.info(
                "Version already exists, returning existing",
                version=target_version,
                version_id=str(existing_version.id),
            )
            return SyncResult(
                version=existing_version,
                actions=actions,
                tarball_uri=existing_version.tarball_uri,
                commit_sha=commit_sha,
            )

        # Create RegistryVersion record with tarball URI
        version_create = RegistryVersionCreate(
            repository_id=db_repo.id,
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

        # Populate RegistryIndex entries
        await versions_service.populate_index_from_manifest(version, commit=False)

        # Commit all changes if requested
        if commit:
            await self.session.commit()
            await self.session.refresh(version)
        else:
            await self.session.flush()
            await self.session.refresh(version)

        self.logger.info(
            "Registry sync v2 (via workflow) completed",
            version_id=str(version.id),
            version=target_version,
            num_actions=len(actions),
            tarball_uri=tarball_uri,
        )

        return SyncResult(
            version=version,
            actions=actions,
            tarball_uri=tarball_uri,
            commit_sha=commit_sha,
        )
