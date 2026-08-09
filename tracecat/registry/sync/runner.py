"""RegistrySyncRunner - Orchestrates sandboxed registry sync phases.

This module implements the core logic for syncing a registry repository
with nsjail sandboxing. It coordinates six phases:

1. SSH host-key acquisition (fresh nsjail + network, no credentials)
2. Git clone (fresh nsjail, network + scoped SSH agent) - for git origins only
3. Package install (nsjail + network) - install dependencies
4. Action discovery (nsjail, NO network) - import and discover actions
5. Artifact packaging (fresh nsjail, NO network)
6. Artifact upload (trusted worker code, outside nsjail)

Security model:
- The host-key jail has network access but no SSH agent or worker credentials
- A dedicated one-key SSH agent socket is exposed ONLY to the clone jail
- DB credentials are NEVER passed to sandbox
- Discovery and packaging have network disabled
- Enabling nsjail globally makes registry sync fail closed when it is unavailable
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
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.exceptions import RegistryError
from tracecat.git.utils import parse_git_url
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.artifact_keys import get_artifact_s3_key
from tracecat.registry.constants import PLATFORM_REGISTRY_NAMESPACE
from tracecat.registry.sync.artifact import (
    RegistryArtifactBuildError,
    RegistryArtifactBuildResult,
    build_artifact_from_path,
    get_builtin_registry_source_path,
    upload_squashfs_venv,
)
from tracecat.registry.sync.prebuilt import load_prebuilt_builtin_registry_manifest
from tracecat.registry.sync.sandbox import RegistrySyncSandbox
from tracecat.registry.sync.schemas import RegistrySyncRequest, RegistrySyncResult
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess
from tracecat.sandbox.utils import is_nsjail_available
from tracecat.secrets.service import SecretsService
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

    def __init__(self, message: str, *, non_retryable: bool = False) -> None:
        super().__init__(message)
        self.non_retryable = non_retryable


_NON_RETRYABLE_DISCOVERY_ERROR_PREFIXES = ("Failed to load template action from ",)


def _is_non_retryable_discovery_error(exc: BaseException) -> bool:
    if not isinstance(exc, RegistryError):
        return False
    message = str(exc)
    return any(
        message.startswith(prefix) for prefix in _NON_RETRYABLE_DISCOVERY_ERROR_PREFIXES
    )


def _build_validation_failure_message(
    validation_errors: dict[str, list[RegistryActionValidationErrorInfo]],
) -> str:
    total_errors = sum(len(errs) for errs in validation_errors.values())
    action_name = next(iter(validation_errors), "<unknown>")
    first_error = (
        validation_errors[action_name][0] if validation_errors[action_name] else None
    )

    first_detail = ""
    if first_error is not None and first_error.details:
        first_detail = first_error.details[0]

    suffix = f" First error in '{action_name}': {first_detail}" if first_detail else ""
    return (
        f"Registry sync validation failed: {total_errors} validation error(s).{suffix}"
    )


class RegistrySyncValidationError(RegistrySyncRunnerError):
    """Raised when action discovery returns validation errors."""

    def __init__(
        self,
        validation_errors: dict[str, list[RegistryActionValidationErrorInfo]],
    ) -> None:
        super().__init__(_build_validation_failure_message(validation_errors))
        self.validation_errors = validation_errors


class RegistrySyncRunner:
    """Orchestrates all phases of sandboxed registry sync.

    This runner executes on the ExecutorWorker and handles:
    - Git operations (with SSH credentials)
    - Package installation (sandboxed with network)
    - Action discovery (sandboxed without network)
    - SquashFS artifact creation and upload
    """

    def __init__(
        self,
        install_timeout: int | None = None,
        discover_timeout: int | None = None,
        clone_timeout: int | None = None,
    ):
        """Initialize the runner.

        Args:
            install_timeout: Timeout for package installation (default from config).
            discover_timeout: Timeout for action discovery (default from config).
            clone_timeout: Timeout for Git clone operations (default from config).
        """
        self.install_timeout = (
            install_timeout or config.TRACECAT__REGISTRY_SYNC_INSTALL_TIMEOUT
        )
        self.discover_timeout = (
            discover_timeout or config.TRACECAT__REGISTRY_SYNC_DISCOVER_TIMEOUT
        )
        self.clone_timeout = (
            clone_timeout or config.TRACECAT__REGISTRY_SYNC_CLONE_TIMEOUT
        )
        self._nsjail_disabled = config.TRACECAT__DISABLE_NSJAIL
        nsjail_available = not self._nsjail_disabled and is_nsjail_available()
        self._sandbox = RegistrySyncSandbox() if nsjail_available else None
        self._nsjail_enabled_but_unavailable = (
            not self._nsjail_disabled and not nsjail_available
        )

    async def run(self, request: RegistrySyncRequest) -> RegistrySyncResult:
        """Execute the full registry sync flow.

        Args:
            request: Sync request with repository details.

        Returns:
            RegistrySyncResult with discovered actions and artifact URI.

        Raises:
            RegistrySyncRunnerError: If any phase fails.
        """
        if self._nsjail_enabled_but_unavailable:
            raise RegistrySyncRunnerError(
                "Registry sync requires nsjail, but nsjail is unavailable on this "
                "ExecutorWorker"
            )

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

            # Phases 1-2: Resolve the package path. Git origins first acquire
            # host keys and then clone in separate jails.
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
                ssh_key = await self._fetch_registry_ssh_key(request.organization_id)
                try:
                    if self._sandbox is not None:
                        try:
                            (
                                package_path,
                                commit_sha,
                            ) = await self._sandbox.clone_repository(
                                git_url=request.git_url,
                                commit_sha=request.commit_sha,
                                ssh_key=ssh_key,
                                work_dir=work_dir,
                                timeout_seconds=self.clone_timeout,
                            )
                        except Exception as exc:
                            raise GitCloneError(
                                f"Sandboxed Git clone failed: {exc}"
                            ) from exc
                    else:
                        logger.warning(
                            "NsJail is explicitly disabled; registry Git clone is "
                            "not sandboxed",
                            disable_nsjail=self._nsjail_disabled,
                        )
                        package_path, commit_sha = await self._clone_repository(
                            git_url=request.git_url,
                            commit_sha=request.commit_sha,
                            ssh_key=ssh_key,
                            work_dir=work_dir,
                        )
                finally:
                    # Drop the worker's private-key reference as soon as the
                    # credential-scoped clone phase exits.
                    ssh_key = ""
            else:
                raise RegistrySyncRunnerError(
                    f"Unknown origin type: {request.origin_type}"
                )

            logger.info(
                "Package path resolved",
                origin_type=request.origin_type,
                package_path=str(package_path),
                sandboxed=self._sandbox is not None,
            )

            storage_namespace = request.storage_namespace or PLATFORM_REGISTRY_NAMESPACE

            artifact_output_dir = work_dir / "artifact"
            sandbox = self._sandbox
            if sandbox is not None:
                # Phase 3: Install with package-network access but no SSH agent.
                installed_site_packages = await sandbox.install_package(
                    package_path=package_path,
                    output_dir=artifact_output_dir,
                    timeout_seconds=self.install_timeout,
                )
                artifact_result: RegistryArtifactBuildResult | None = None
                logger.info(
                    "Registry package installed",
                    site_packages_path=str(installed_site_packages),
                )
            else:
                # The compatibility path still builds the artifact in one step.
                artifact_result = await self._build_execution_artifact(
                    package_path=package_path,
                    output_dir=artifact_output_dir,
                )
                installed_site_packages = artifact_result.site_packages_path
                logger.info(
                    "Registry artifact built",
                    squashfs_path=str(artifact_result.squashfs_path),
                    artifact_size_bytes=artifact_result.artifact_size_bytes,
                )

            # Phase 4: Discover actions from the installed packages, or load the
            # release-built manifest for builtin registries.
            prebuilt_manifest = load_prebuilt_builtin_registry_manifest(
                origin=request.origin,
                target_version=request.target_version,
                storage_namespace=storage_namespace,
            )
            actions: list[RegistryActionCreate] | None = None
            validation_errors: dict[str, list[RegistryActionValidationErrorInfo]] = {}
            if prebuilt_manifest is not None:
                try:
                    actions = prebuilt_manifest.to_action_creates(
                        repository_id=request.repository_id,
                        origin=request.origin,
                    )
                    validation_errors = {}
                    logger.info(
                        "Loaded prebuilt builtin registry manifest",
                        num_actions=len(actions),
                        target_version=request.target_version,
                    )
                except Exception as e:
                    logger.warning(
                        "Ignoring prebuilt registry manifest that could not be converted",
                        origin=request.origin,
                        target_version=request.target_version,
                        error=str(e),
                    )
                    prebuilt_manifest = None

            if actions is None:
                actions, validation_errors = await self._discover_actions(
                    repository_id=request.repository_id,
                    origin=request.origin,
                    commit_sha=commit_sha,
                    validate=request.validate_actions,
                    git_repo_package_name=request.git_repo_package_name,
                    organization_id=request.organization_id,
                    installed_site_packages=installed_site_packages,
                    package_name=self._resolve_package_name(request, package_path),
                )

                logger.info(
                    "Actions discovered",
                    num_actions=len(actions),
                    num_validation_errors=len(validation_errors),
                )

            if validation_errors:
                raise RegistrySyncValidationError(validation_errors)

            # Phase 5: Package in a fresh no-network jail only after discovery
            # and validation have succeeded.
            if artifact_result is None:
                if sandbox is None or installed_site_packages is None:
                    raise RegistrySyncRunnerError(
                        "Sandboxed registry packaging requires installed packages"
                    )
                artifact_result = await sandbox.package_site_packages(
                    site_packages=installed_site_packages,
                    output_dir=artifact_output_dir,
                    timeout_seconds=self.install_timeout,
                )
                logger.info(
                    "Registry artifact built",
                    squashfs_path=str(artifact_result.squashfs_path),
                    artifact_size_bytes=artifact_result.artifact_size_bytes,
                )

            # Phase 6: Upload from trusted worker code.
            artifact_uri = await self._upload_squashfs(
                squashfs_path=artifact_result.squashfs_path,
                repository_origin=request.origin,
                commit_sha=commit_sha,
                storage_namespace=request.storage_namespace,
            )

            logger.info(
                "Registry artifact uploaded",
                artifact_uri=artifact_uri,
            )

            return RegistrySyncResult(
                actions=actions,
                artifact_uri=artifact_uri,
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
        except RegistryArtifactBuildError as e:
            raise RegistrySyncRunnerError(str(e)) from e

    async def _fetch_registry_ssh_key(self, organization_id: UUID | None) -> str:
        """Fetch the org-scoped registry SSH key on the ExecutorWorker."""
        if organization_id is None:
            raise GitCloneError(
                "Git repository sync requires organization_id to fetch the SSH key"
            )

        role = Role(
            type="service",
            service_id="tracecat-service",
            organization_id=organization_id,
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
        )

        async with SecretsService.with_session(role=role) as secrets_service:
            try:
                secret = await secrets_service.get_ssh_key(target="registry")
            except Exception as exc:
                raise GitCloneError(
                    f"Failed to retrieve SSH key for git operations: {exc}. "
                    "Ensure a 'github-ssh-key' secret exists in your organization."
                ) from exc
        return secret.get_secret_value()

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
        git_timeout = self.clone_timeout

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

    async def _build_execution_artifact(
        self,
        package_path: Path,
        output_dir: Path,
    ) -> RegistryArtifactBuildResult:
        """Build a SquashFS registry artifact from the package.

        Args:
            package_path: Path to the package directory.
            output_dir: Directory for output files.

        Returns:
            RegistryArtifactBuildResult with build metadata.

        Raises:
            RegistryArtifactBuildError: If build fails.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        if self._sandbox is not None:
            return await self._sandbox.build_execution_artifact(
                package_path=package_path,
                output_dir=output_dir,
                timeout_seconds=self.install_timeout,
            )

        logger.warning(
            "NsJail is explicitly disabled; registry package installation is not "
            "sandboxed",
            disable_nsjail=self._nsjail_disabled,
        )
        return await build_artifact_from_path(package_path, output_dir)

    @staticmethod
    def _resolve_package_name(
        request: RegistrySyncRequest,
        package_path: Path,
    ) -> str:
        """Resolve the import package name used by sandboxed discovery."""
        if request.origin_type == "builtin":
            return request.origin
        if request.git_repo_package_name:
            return request.git_repo_package_name
        if request.git_url:
            try:
                return parse_git_url(request.git_url).repo
            except ValueError as exc:
                raise RegistrySyncRunnerError(
                    "Cannot resolve package name from invalid registry Git URL"
                ) from exc
        return package_path.name

    async def _discover_actions(
        self,
        repository_id: UUID,
        origin: str,
        commit_sha: str | None = None,
        validate: bool = False,
        git_repo_package_name: str | None = None,
        organization_id: UUID | None = None,
        installed_site_packages: Path | None = None,
        package_name: str | None = None,
    ) -> tuple[
        list[RegistryActionCreate], dict[str, list[RegistryActionValidationErrorInfo]]
    ]:
        """Discover actions from the repository.

        This uses NsJail without network when nsjail is enabled globally and
        retains the legacy subprocess path when nsjail is explicitly disabled.

        Args:
            repository_id: Database repository ID.
            origin: Repository origin (e.g., "tracecat_registry", "local", or git URL).
            commit_sha: Optional commit SHA to load for remote repositories.
            validate: Whether to validate template actions.
            git_repo_package_name: Optional override for git repository package name.

        Returns:
            Tuple of (actions, validation_errors).

        Raises:
            ActionDiscoveryError: If discovery fails.
        """

        try:
            if self._sandbox is not None:
                if installed_site_packages is None or package_name is None:
                    raise RegistryError(
                        "Sandboxed discovery requires installed site-packages and a package name"
                    )
                result = await self._sandbox.discover_actions(
                    site_packages=installed_site_packages,
                    origin=origin,
                    package_name=package_name,
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    validate=validate,
                    organization_id=organization_id,
                    timeout_seconds=self.discover_timeout,
                )
                return result.actions, result.validation_errors

            logger.warning(
                "NsJail is explicitly disabled; registry action discovery is not "
                "sandboxed",
                disable_nsjail=self._nsjail_disabled,
            )
            result = await fetch_actions_from_subprocess(
                origin=origin,
                repository_id=repository_id,
                commit_sha=commit_sha,
                validate=validate,
                git_repo_package_name=git_repo_package_name,
                timeout=float(self.discover_timeout),
                organization_id=organization_id,
            )
            return result.actions, result.validation_errors
        except Exception as e:
            raise ActionDiscoveryError(
                f"Failed to discover actions: {e}",
                non_retryable=_is_non_retryable_discovery_error(e),
            ) from e

    async def _upload_squashfs(
        self,
        squashfs_path: Path,
        repository_origin: str,
        commit_sha: str | None,
        storage_namespace: str | None,
    ) -> str:
        """Upload the SquashFS registry artifact to S3.

        Args:
            squashfs_path: Local path to the SquashFS artifact.
            repository_origin: Repository origin for S3 key generation.
            commit_sha: Commit SHA for version string (or timestamp if None).
            storage_namespace: Namespace prefix for artifact storage.

        Returns:
            S3 URI of the uploaded SquashFS artifact.
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
        namespace = storage_namespace or PLATFORM_REGISTRY_NAMESPACE
        s3_key = get_artifact_s3_key(
            organization_id=namespace,
            repository_origin=repository_origin,
            version=version,
        )

        # Upload
        return await upload_squashfs_venv(
            squashfs_path=squashfs_path,
            key=s3_key,
            bucket=bucket,
        )
