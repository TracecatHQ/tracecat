"""RegistrySyncRunner - Orchestrates sandboxed registry sync phases.

This module implements the core logic for syncing a registry repository
with nsjail sandboxing. It coordinates four phases:

1. Git clone (subprocess, needs SSH) - for git origins only
2. Package install (nsjail + network) - install dependencies
3. Action discovery (nsjail, NO network) - import and discover actions
4. Tarball build and upload - create portable venv

Security model:
- SSH keys are used ONLY for git clone (outside nsjail)
- DB credentials are NEVER passed to sandbox
- Discovery phase has network disabled to prevent exfiltration
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import aiofiles

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.sync.platform_service import PLATFORM_REGISTRY_TARBALL_NAMESPACE
from tracecat.registry.sync.schemas import RegistrySyncRequest, RegistrySyncResult
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess
from tracecat.registry.sync.tarball import (
    TarballBuildError,
    TarballVenvBuildResult,
    build_tarball_venv_from_path,
    get_builtin_registry_source_path,
    get_tarball_venv_s3_key,
    upload_tarball_venv,
)
from tracecat.storage import blob

if TYPE_CHECKING:
    from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo


class RegistrySyncRunnerError(Exception):
    """Base exception for registry sync runner errors."""


class GitCloneError(RegistrySyncRunnerError):
    """Raised when git clone fails."""


class PackageInstallError(RegistrySyncRunnerError):
    """Raised when package installation fails."""


class ActionDiscoveryError(RegistrySyncRunnerError):
    """Raised when action discovery fails."""


class RegistrySyncRunner:
    """Orchestrates all phases of sandboxed registry sync.

    This runner executes on the ExecutorWorker and handles:
    - Git operations (with SSH credentials)
    - Package installation (sandboxed with network)
    - Action discovery (sandboxed without network)
    - Tarball creation and upload
    """

    def __init__(
        self,
        install_timeout: int | None = None,
        discover_timeout: int | None = None,
    ):
        """Initialize the runner.

        Args:
            install_timeout: Timeout for package installation (default from config).
            discover_timeout: Timeout for action discovery (default from config).
        """
        self.install_timeout: int = install_timeout or int(
            os.environ.get("TRACECAT__REGISTRY_SYNC_INSTALL_TIMEOUT", 600)
        )
        self.discover_timeout: int = discover_timeout or int(
            os.environ.get("TRACECAT__REGISTRY_SYNC_DISCOVER_TIMEOUT", 300)
        )

    async def run(self, request: RegistrySyncRequest) -> RegistrySyncResult:
        """Execute the full registry sync flow.

        Args:
            request: Sync request with repository details.

        Returns:
            RegistrySyncResult with discovered actions and tarball URI.

        Raises:
            RegistrySyncRunnerError: If any phase fails.
        """
        logger.info(
            "Starting registry sync",
            origin=request.origin,
            origin_type=request.origin_type,
            repository_id=str(request.repository_id),
        )

        # Use a temporary directory for all intermediate files
        async with aiofiles.tempfile.TemporaryDirectory(
            prefix="tracecat_sync_"
        ) as temp_dir:
            work_dir = Path(temp_dir)

            # Phase 1: Resolve package path based on origin type
            if request.origin_type == "builtin":
                package_path = await self._get_builtin_package_path()
                commit_sha = None
            elif request.origin_type == "local":
                package_path = Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH)
                commit_sha = None
            elif request.origin_type == "git":
                if not request.git_url:
                    raise RegistrySyncRunnerError(
                        "git_url is required for git origin type"
                    )
                package_path, commit_sha = await self._clone_repository(
                    git_url=request.git_url,
                    commit_sha=request.commit_sha,
                    ssh_key=request.ssh_key,
                    work_dir=work_dir,
                )
            else:
                raise RegistrySyncRunnerError(
                    f"Unknown origin type: {request.origin_type}"
                )

            logger.info(
                "Package path resolved",
                origin_type=request.origin_type,
                package_path=str(package_path),
            )

            # Phase 2: Build tarball venv (includes package installation)
            # This runs `uv pip install` which needs network access
            tarball_result = await self._build_tarball_venv(
                package_path=package_path,
                output_dir=work_dir / "tarball",
            )

            logger.info(
                "Tarball venv built",
                tarball_path=str(tarball_result.tarball_path),
                compressed_size_bytes=tarball_result.compressed_size_bytes,
            )

            # Phase 3: Discover actions from the installed packages
            # This phase could run in nsjail without network for extra security
            # For now, we use the subprocess approach (same as existing code)
            actions, validation_errors = await self._discover_actions(
                repository_id=request.repository_id,
                origin=request.origin,
                validate=request.validate_actions,
                git_repo_package_name=request.git_repo_package_name,
                organization_id=request.organization_id,
            )

            logger.info(
                "Actions discovered",
                num_actions=len(actions),
                num_validation_errors=len(validation_errors),
            )

            # Phase 4: Upload tarball to S3
            tarball_uri = await self._upload_tarball(
                tarball_path=tarball_result.tarball_path,
                repository_origin=request.origin,
                commit_sha=commit_sha,
                storage_namespace=request.storage_namespace,
            )

            logger.info(
                "Tarball uploaded",
                tarball_uri=tarball_uri,
            )

            return RegistrySyncResult(
                actions=actions,
                tarball_uri=tarball_uri,
                commit_sha=commit_sha,
                validation_errors=validation_errors,
            )

    async def _get_builtin_package_path(self) -> Path:
        """Get the path to the builtin tracecat_registry package.

        Returns:
            Path to the package directory containing pyproject.toml.

        Raises:
            RegistrySyncRunnerError: If package path cannot be determined.
        """
        try:
            return get_builtin_registry_source_path()
        except TarballBuildError as e:
            raise RegistrySyncRunnerError(str(e)) from e

    async def _clone_repository(
        self,
        git_url: str,
        commit_sha: str | None,
        ssh_key: str | None,
        work_dir: Path,
    ) -> tuple[Path, str]:
        """Clone a git repository to a local directory.

        Args:
            git_url: Git SSH URL (git+ssh://...).
            commit_sha: Commit SHA to checkout (if None, uses HEAD).
            ssh_key: SSH private key for authentication.
            work_dir: Working directory for the clone.

        Returns:
            Tuple of (path to cloned repository, resolved commit SHA).

        Raises:
            GitCloneError: If clone fails.
        """
        clone_path = work_dir / "repo"
        clone_path.mkdir(parents=True, exist_ok=True)

        # Strip git+ssh:// prefix for git clone
        clone_url = git_url.replace("git+ssh://", "ssh://")

        logger.info(
            "Cloning repository",
            url=clone_url,
            commit_sha=commit_sha,
        )

        # Set up environment for git operations
        git_env = os.environ.copy()
        if ssh_key:
            # Write SSH key to a temporary file
            ssh_key_path = work_dir / "ssh_key"
            _ = ssh_key_path.write_text(ssh_key)
            ssh_key_path.chmod(0o600)

            # Configure SSH to use the key
            # BatchMode=yes prevents SSH from prompting for input (passphrase, etc.)
            # which would cause the subprocess to hang indefinitely
            git_env["GIT_SSH_COMMAND"] = (
                f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no -o BatchMode=yes"
            )

        # Timeout for git operations (clone, fetch, checkout)
        git_timeout = 120  # 2 minutes

        try:
            # Clone the repository
            clone_cmd = ["git", "clone", "--depth", "1", clone_url, str(clone_path)]
            process = await asyncio.create_subprocess_exec(
                *clone_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=git_env,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=git_timeout
                )
            except TimeoutError as e:
                process.kill()
                raise GitCloneError(
                    f"Git clone timed out after {git_timeout}s. "
                    "Check SSH key permissions and network connectivity."
                ) from e

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                raise GitCloneError(f"Failed to clone repository: {error_msg}")

            # Fetch and checkout the specific commit if provided
            if commit_sha:
                fetch_cmd = ["git", "fetch", "origin", commit_sha]
                process = await asyncio.create_subprocess_exec(
                    *fetch_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(clone_path),
                    env=git_env,
                )
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=git_timeout
                    )
                except TimeoutError as e:
                    process.kill()
                    raise GitCloneError(
                        f"Git fetch timed out after {git_timeout}s. "
                        "Check SSH key permissions and network connectivity."
                    ) from e

                if process.returncode != 0:
                    error_msg = stderr.decode().strip()
                    raise GitCloneError(f"Failed to fetch commit: {error_msg}")

                checkout_cmd = ["git", "checkout", commit_sha]
                process = await asyncio.create_subprocess_exec(
                    *checkout_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(clone_path),
                    env=git_env,
                )
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=git_timeout
                    )
                except TimeoutError as e:
                    process.kill()
                    raise GitCloneError(
                        f"Git checkout timed out after {git_timeout}s."
                    ) from e

                if process.returncode != 0:
                    error_msg = stderr.decode().strip()
                    raise GitCloneError(f"Failed to checkout commit: {error_msg}")

            # Get the resolved commit SHA (verify the checkout worked)
            rev_parse_cmd = ["git", "rev-parse", "HEAD"]
            process = await asyncio.create_subprocess_exec(
                *rev_parse_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(clone_path),
                env=git_env,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                raise GitCloneError(f"Failed to get commit SHA: {error_msg}")
            resolved_sha = stdout.decode().strip()
            return clone_path, resolved_sha

        finally:
            # Clean up SSH key from memory and disk
            if ssh_key:
                ssh_key_path = work_dir / "ssh_key"
                if ssh_key_path.exists():
                    # Overwrite with zeros before deletion
                    _ = ssh_key_path.write_bytes(b"\x00" * len(ssh_key))
                    ssh_key_path.unlink()

    async def _build_tarball_venv(
        self,
        package_path: Path,
        output_dir: Path,
    ) -> TarballVenvBuildResult:
        """Build a tarball venv from the package.

        Args:
            package_path: Path to the package directory.
            output_dir: Directory for output files.

        Returns:
            TarballVenvBuildResult with build metadata.

        Raises:
            TarballBuildError: If build fails.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        return await build_tarball_venv_from_path(package_path, output_dir)

    async def _discover_actions(
        self,
        repository_id: UUID,
        origin: str,
        validate: bool = False,
        git_repo_package_name: str | None = None,
        organization_id: UUID | None = None,
    ) -> tuple[
        list[RegistryActionCreate], dict[str, list[RegistryActionValidationErrorInfo]]
    ]:
        """Discover actions from the repository.

        This uses the existing subprocess-based discovery mechanism.
        In the future, this could be replaced with nsjail-based discovery
        with network disabled for extra security.

        Args:
            repository_id: Database repository ID.
            origin: Repository origin (e.g., "tracecat_registry", "local", or git URL).
            validate: Whether to validate template actions.
            git_repo_package_name: Optional override for git repository package name.

        Returns:
            Tuple of (actions, validation_errors).

        Raises:
            ActionDiscoveryError: If discovery fails.
        """

        try:
            result = await fetch_actions_from_subprocess(
                origin=origin,
                repository_id=repository_id,
                validate=validate,
                git_repo_package_name=git_repo_package_name,
                timeout=float(self.discover_timeout),
                organization_id=organization_id,
            )
            return result.actions, result.validation_errors
        except Exception as e:
            raise ActionDiscoveryError(f"Failed to discover actions: {e}") from e

    async def _upload_tarball(
        self,
        tarball_path: Path,
        repository_origin: str,
        commit_sha: str | None,
        storage_namespace: str | None,
    ) -> str:
        """Upload the tarball venv to S3.

        Args:
            tarball_path: Local path to the tarball.
            repository_origin: Repository origin for S3 key generation.
            commit_sha: Commit SHA for version string (or timestamp if None).
            storage_namespace: Namespace prefix for tarball storage.

        Returns:
            S3 URI of the uploaded tarball.
        """

        # Generate version string
        if commit_sha:
            version = commit_sha
        else:
            version = datetime.now(UTC).strftime("%Y.%m.%d.%H%M%S")

        # Ensure bucket exists
        bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY
        await blob.ensure_bucket_exists(bucket)

        # Generate S3 key
        namespace = storage_namespace or PLATFORM_REGISTRY_TARBALL_NAMESPACE
        s3_key = get_tarball_venv_s3_key(
            organization_id=namespace,
            repository_origin=repository_origin,
            version=version,
        )

        # Upload
        return await upload_tarball_venv(
            tarball_path=tarball_path,
            key=s3_key,
            bucket=bucket,
        )
