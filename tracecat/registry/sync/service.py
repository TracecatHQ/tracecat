"""Registry sync service for v2 versioned registry flow.

This module orchestrates the full sync flow:
1. Fetch actions from subprocess (existing)
2. Build manifest from actions
3. Build wheel from package and upload to S3/MinIO
4. Create RegistryVersion record with wheel URI
5. Populate RegistryIndex entries

Note: Wheel building is mandatory. If the wheel build fails, no version is
created. The code must be packagable into a valid wheel to be considered
a valid registry version.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

from tracecat import config
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess
from tracecat.registry.sync.wheel import (
    WheelBuildError,
    WheelBuildResult,
    build_builtin_registry_wheel,
    build_wheel_from_git,
    build_wheel_from_path,
    get_wheel_s3_key,
    upload_wheel,
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


class SyncResult:
    """Result of a registry sync operation."""

    def __init__(
        self,
        version: RegistryVersion,
        actions: list[RegistryActionCreate],
        wheel_uri: str,
        commit_sha: str | None = None,
    ):
        self.version = version
        self.actions = actions
        self.wheel_uri = wheel_uri
        self.commit_sha = commit_sha

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
    - Building and uploading wheels to S3
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

        This creates an immutable RegistryVersion with a wheel artifact.
        If the wheel build fails, no version is created - the code must be
        packagable to be considered a valid version.

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
            WheelBuildError: If wheel building fails
        """
        self.logger.info(
            "Starting v2 registry sync",
            repository=db_repo.origin,
            target_version=target_version,
            target_commit_sha=target_commit_sha,
        )

        # Step 1: Fetch actions from subprocess
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
            # Existing versions must have a wheel_uri - if not, it's legacy
            # data from before wheel building was mandatory
            if existing_version.wheel_uri is None:
                raise RegistrySyncError(
                    f"Version {target_version} exists but has no wheel. "
                    "Delete the version and re-sync to create a valid version with a wheel."
                )
            self.logger.info(
                "Version already exists, returning existing",
                version=target_version,
                version_id=str(existing_version.id),
            )
            return SyncResult(
                version=existing_version,
                actions=actions,
                wheel_uri=existing_version.wheel_uri,
                commit_sha=commit_sha,
            )

        # Step 5: Build wheel first - if this fails, we don't create a version
        # The code must be packagable to be considered a valid version
        wheel_uri = await self._build_and_upload_wheel(
            db_repo=db_repo,
            version_string=target_version,
            commit_sha=commit_sha,
            ssh_env=ssh_env,
        )

        # Step 6: Create RegistryVersion record with wheel URI
        version_create = RegistryVersionCreate(
            repository_id=db_repo.id,
            version=target_version,
            commit_sha=commit_sha,
            manifest=manifest,
            wheel_uri=wheel_uri,
        )
        version = await versions_service.create_version(version_create, commit=False)

        self.logger.info(
            "Created registry version",
            version_id=str(version.id),
            version=target_version,
            wheel_uri=wheel_uri,
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
            wheel_uri=wheel_uri,
        )

        return SyncResult(
            version=version,
            actions=actions,
            wheel_uri=wheel_uri,
            commit_sha=commit_sha,
        )

    async def _build_and_upload_wheel(
        self,
        db_repo: RegistryRepository,
        version_string: str,
        commit_sha: str | None,
        ssh_env: SshEnv | None = None,
    ) -> str:
        """Build a wheel and upload it to S3.

        Args:
            db_repo: The repository to build the wheel from
            version_string: Version string for the S3 key path
            commit_sha: Git commit SHA (required for git repositories)
            ssh_env: SSH environment for git operations

        Returns:
            S3 URI of the uploaded wheel

        Raises:
            WheelBuildError: If wheel building or upload fails
        """
        async with aiofiles.tempfile.TemporaryDirectory(
            prefix="tracecat_wheel_"
        ) as temp_dir:
            output_dir = Path(temp_dir)

            # Build wheel based on origin type
            wheel_result: WheelBuildResult
            if db_repo.origin == DEFAULT_REGISTRY_ORIGIN:
                wheel_result = await build_builtin_registry_wheel(output_dir)
            elif db_repo.origin == DEFAULT_LOCAL_REGISTRY_ORIGIN:
                local_path = Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH)
                wheel_result = await build_wheel_from_path(local_path, output_dir)
            elif db_repo.origin.startswith("git+ssh://"):
                if commit_sha is None:
                    raise WheelBuildError("commit_sha is required for git repositories")
                wheel_result = await build_wheel_from_git(
                    git_url=db_repo.origin,
                    commit_sha=commit_sha,
                    env=ssh_env,
                    output_dir=output_dir,
                )
            else:
                raise WheelBuildError(
                    f"Unsupported origin for wheel building: {db_repo.origin}"
                )

            self.logger.info(
                "Wheel built successfully",
                wheel_name=wheel_result.wheel_name,
                package=wheel_result.package_name,
                version=wheel_result.version,
            )

            # Generate S3 key for the wheel
            s3_key = get_wheel_s3_key(
                organization_id=str(config.TRACECAT__DEFAULT_ORG_ID),
                repository_origin=db_repo.origin,
                version=version_string,
                wheel_name=wheel_result.wheel_name,
            )

            # Ensure bucket exists
            bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY
            await blob.ensure_bucket_exists(bucket)

            # Upload wheel to S3
            wheel_uri = await upload_wheel(
                wheel_path=wheel_result.wheel_path,
                key=s3_key,
                bucket=bucket,
            )

            return wheel_uri

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
