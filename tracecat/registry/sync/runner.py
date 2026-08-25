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
from dataclasses import dataclass, field
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
from tracecat.registry.sync.schemas import (
    RegistrySyncRequest,
    RegistrySyncResult,
    SyncResultSuccess,
)
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


# Deterministic content/configuration failures that retrying cannot fix.
_NON_RETRYABLE_DISCOVERY_ERROR_PREFIXES = (
    "Failed to load template action from ",
    # Wrong `git_repo_package_name` (ModuleNotFoundError in the sync subprocess).
    "No module named ",
)


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


@dataclass(frozen=True, slots=True)
class _ResolvedPackage:
    """Package source and immutable revision selected for one sync."""

    path: Path
    commit_sha: str | None


@dataclass(frozen=True, slots=True)
class _SandboxedBackend:
    """Available NsJail backend selected for the full sync."""

    sandbox: RegistrySyncSandbox = field(default_factory=RegistrySyncSandbox)


@dataclass(frozen=True, slots=True)
class _UnsandboxedBackend:
    """Explicit no-NsJail compatibility backend."""


@dataclass(frozen=True, slots=True)
class _UnavailableBackend:
    """Required NsJail backend that is unavailable on this worker."""


type _RegistrySyncBackend = (
    _SandboxedBackend | _UnsandboxedBackend | _UnavailableBackend
)


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
        if config.TRACECAT__DISABLE_NSJAIL:
            self._backend: _RegistrySyncBackend = _UnsandboxedBackend()
        elif is_nsjail_available():
            self._backend = _SandboxedBackend()
        else:
            self._backend = _UnavailableBackend()

    async def run(self, request: RegistrySyncRequest) -> RegistrySyncResult:
        """Execute the full registry sync flow.

        Args:
            request: Sync request with repository details.

        Returns:
            RegistrySyncResult with discovered actions and artifact URI.

        Raises:
            RegistrySyncRunnerError: If any phase fails.
        """
        if (
            request.origin_type == "local"
            and not config.TRACECAT__LOCAL_REPOSITORY_ENABLED
        ):
            raise RegistrySyncRunnerError(
                "Local repository is not enabled on this instance. "
                "Please set TRACECAT__LOCAL_REPOSITORY_ENABLED=true."
            )
        if isinstance(self._backend, _UnavailableBackend):
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
            match self._backend:
                case _SandboxedBackend(sandbox=sandbox):
                    return await self._run_sandboxed(request, work_dir, sandbox)
                case _UnsandboxedBackend():
                    return await self._run_unsandboxed(request, work_dir)
                case _UnavailableBackend():
                    raise AssertionError(
                        "Unavailable backend passed the fail-closed guard"
                    )

    async def _run_sandboxed(
        self,
        request: RegistrySyncRequest,
        work_dir: Path,
        sandbox: RegistrySyncSandbox,
    ) -> RegistrySyncResult:
        """Execute all registry phases with the selected NsJail backend."""
        resolved = await self._resolve_sandboxed_package(request, work_dir, sandbox)
        self._log_resolved_package(request, resolved, sandboxed=True)
        output_dir = work_dir / "artifact"

        installed_site_packages = await sandbox.install_package(
            package_path=resolved.path,
            output_dir=output_dir,
            timeout_seconds=self.install_timeout,
        )
        logger.info(
            "Registry package installed",
            site_packages_path=str(installed_site_packages),
        )

        discovery = self._load_prebuilt_actions(request)
        if discovery is None:
            discovery = await self._discover_sandboxed_actions(
                request=request,
                resolved=resolved,
                installed_site_packages=installed_site_packages,
                sandbox=sandbox,
            )
        self._raise_for_validation_errors(discovery)

        artifact_result = await sandbox.package_site_packages(
            site_packages=installed_site_packages,
            output_dir=output_dir,
            timeout_seconds=self.install_timeout,
        )
        self._log_built_artifact(artifact_result)
        return await self._finalize_sync(
            request=request,
            resolved=resolved,
            discovery=discovery,
            artifact_result=artifact_result,
        )

    async def _run_unsandboxed(
        self,
        request: RegistrySyncRequest,
        work_dir: Path,
    ) -> RegistrySyncResult:
        """Execute the explicit no-NsJail compatibility flow."""
        resolved = await self._resolve_unsandboxed_package(request, work_dir)
        self._log_resolved_package(request, resolved, sandboxed=False)
        artifact_result = await self._build_unsandboxed_execution_artifact(
            package_path=resolved.path,
            output_dir=work_dir / "artifact",
        )
        self._log_built_artifact(artifact_result)

        discovery = self._load_prebuilt_actions(request)
        if discovery is None:
            discovery = await self._discover_unsandboxed_actions(
                request, resolved.commit_sha
            )
        self._raise_for_validation_errors(discovery)
        return await self._finalize_sync(
            request=request,
            resolved=resolved,
            discovery=discovery,
            artifact_result=artifact_result,
        )

    async def _resolve_sandboxed_package(
        self,
        request: RegistrySyncRequest,
        work_dir: Path,
        sandbox: RegistrySyncSandbox,
    ) -> _ResolvedPackage:
        """Resolve a package source without leaving the sandboxed backend."""
        if request.origin_type == "builtin":
            return _ResolvedPackage(await self._get_builtin_package_path(), None)
        if request.origin_type == "local":
            return _ResolvedPackage(
                Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH),
                None,
            )
        if not request.git_url:
            raise RegistrySyncRunnerError("git_url is required for git origin type")

        ssh_key = await self._fetch_registry_ssh_key(request.organization_id)
        try:
            try:
                package_path, commit_sha = await sandbox.clone_repository(
                    git_url=request.git_url,
                    commit_sha=request.commit_sha,
                    ssh_key=ssh_key,
                    work_dir=work_dir,
                    timeout_seconds=self.clone_timeout,
                )
            except Exception as exc:
                raise GitCloneError(f"Sandboxed Git clone failed: {exc}") from exc
        finally:
            ssh_key = ""
        return _ResolvedPackage(package_path, commit_sha)

    async def _resolve_unsandboxed_package(
        self,
        request: RegistrySyncRequest,
        work_dir: Path,
    ) -> _ResolvedPackage:
        """Resolve a package source with the explicit compatibility backend."""
        if request.origin_type == "builtin":
            return _ResolvedPackage(await self._get_builtin_package_path(), None)
        if request.origin_type == "local":
            return _ResolvedPackage(
                Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH),
                None,
            )
        if not request.git_url:
            raise RegistrySyncRunnerError("git_url is required for git origin type")

        logger.warning(
            "NsJail is explicitly disabled; registry Git clone is not sandboxed",
            disable_nsjail=True,
        )
        ssh_key = await self._fetch_registry_ssh_key(request.organization_id)
        try:
            package_path, commit_sha = await self._clone_repository(
                git_url=request.git_url,
                commit_sha=request.commit_sha,
                ssh_key=ssh_key,
                work_dir=work_dir,
            )
        finally:
            ssh_key = ""
        return _ResolvedPackage(package_path, commit_sha)

    def _load_prebuilt_actions(
        self,
        request: RegistrySyncRequest,
    ) -> SyncResultSuccess | None:
        """Load a release-built manifest when one matches this request."""
        storage_namespace = request.storage_namespace or PLATFORM_REGISTRY_NAMESPACE
        prebuilt_manifest = load_prebuilt_builtin_registry_manifest(
            origin=request.origin,
            target_version=request.target_version,
            storage_namespace=storage_namespace,
        )
        if prebuilt_manifest is None:
            return None
        try:
            actions = prebuilt_manifest.to_action_creates(
                repository_id=request.repository_id,
                origin=request.origin,
            )
        except Exception as exc:
            logger.warning(
                "Ignoring prebuilt registry manifest that could not be converted",
                origin=request.origin,
                target_version=request.target_version,
                error=str(exc),
            )
            return None
        logger.info(
            "Loaded prebuilt builtin registry manifest",
            num_actions=len(actions),
            target_version=request.target_version,
        )
        return SyncResultSuccess(actions=actions)

    @staticmethod
    def _raise_for_validation_errors(discovery: SyncResultSuccess) -> None:
        if discovery.validation_errors:
            raise RegistrySyncValidationError(discovery.validation_errors)

    @staticmethod
    def _log_resolved_package(
        request: RegistrySyncRequest,
        resolved: _ResolvedPackage,
        *,
        sandboxed: bool,
    ) -> None:
        logger.info(
            "Package path resolved",
            origin_type=request.origin_type,
            package_path=str(resolved.path),
            sandboxed=sandboxed,
        )

    @staticmethod
    def _log_built_artifact(artifact_result: RegistryArtifactBuildResult) -> None:
        logger.info(
            "Registry artifact built",
            squashfs_path=str(artifact_result.squashfs_path),
            artifact_size_bytes=artifact_result.artifact_size_bytes,
        )

    async def _finalize_sync(
        self,
        *,
        request: RegistrySyncRequest,
        resolved: _ResolvedPackage,
        discovery: SyncResultSuccess,
        artifact_result: RegistryArtifactBuildResult,
    ) -> RegistrySyncResult:
        """Upload a validated artifact and create the workflow result."""
        artifact_uri = await self._upload_squashfs(
            squashfs_path=artifact_result.squashfs_path,
            repository_origin=request.origin,
            commit_sha=resolved.commit_sha,
            storage_namespace=request.storage_namespace,
        )
        logger.info("Registry artifact uploaded", artifact_uri=artifact_uri)
        return RegistrySyncResult(
            actions=discovery.actions,
            artifact_uri=artifact_uri,
            commit_sha=resolved.commit_sha,
            validation_errors=discovery.validation_errors,
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

    async def _build_unsandboxed_execution_artifact(
        self,
        package_path: Path,
        output_dir: Path,
    ) -> RegistryArtifactBuildResult:
        """Build an artifact with the explicit no-NsJail compatibility path.

        Args:
            package_path: Path to the package directory.
            output_dir: Directory for output files.

        Returns:
            RegistryArtifactBuildResult with build metadata.

        Raises:
            RegistryArtifactBuildError: If build fails.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "NsJail is explicitly disabled; registry package installation is not "
            "sandboxed",
            disable_nsjail=True,
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

    async def _discover_sandboxed_actions(
        self,
        *,
        request: RegistrySyncRequest,
        resolved: _ResolvedPackage,
        installed_site_packages: Path,
        sandbox: RegistrySyncSandbox,
    ) -> SyncResultSuccess:
        """Discover installed actions in the selected no-network jail.

        Args:
            request: Registry sync request.
            resolved: Package source and resolved revision.
            installed_site_packages: Isolated installation output.
            sandbox: Backend selected for the full sync.

        Returns:
            Typed discovery result.

        Raises:
            ActionDiscoveryError: If discovery fails.
        """
        package_name = self._resolve_package_name(request, resolved.path)
        try:
            result = await sandbox.discover_actions(
                site_packages=installed_site_packages,
                origin=request.origin,
                package_name=package_name,
                repository_id=request.repository_id,
                commit_sha=resolved.commit_sha,
                validate=request.validate_actions,
                organization_id=request.organization_id,
                timeout_seconds=self.discover_timeout,
            )
        except Exception as exc:
            raise ActionDiscoveryError(
                f"Failed to discover actions: {exc}",
                non_retryable=_is_non_retryable_discovery_error(exc),
            ) from exc
        self._log_discovered_actions(result)
        return result

    async def _discover_unsandboxed_actions(
        self,
        request: RegistrySyncRequest,
        commit_sha: str | None,
    ) -> SyncResultSuccess:
        """Discover actions with the explicit subprocess compatibility path.

        Args:
            request: Registry sync request.
            commit_sha: Resolved repository revision, if any.

        Returns:
            Typed discovery result.

        Raises:
            ActionDiscoveryError: If discovery fails.
        """
        try:
            logger.warning(
                "NsJail is explicitly disabled; registry action discovery is not "
                "sandboxed",
                disable_nsjail=True,
            )
            result = await fetch_actions_from_subprocess(
                origin=request.origin,
                repository_id=request.repository_id,
                commit_sha=commit_sha,
                validate=request.validate_actions,
                git_repo_package_name=request.git_repo_package_name,
                timeout=float(self.discover_timeout),
                organization_id=request.organization_id,
            )
        except Exception as exc:
            raise ActionDiscoveryError(
                f"Failed to discover actions: {exc}",
                non_retryable=_is_non_retryable_discovery_error(exc),
            ) from exc
        self._log_discovered_actions(result)
        return result

    @staticmethod
    def _log_discovered_actions(discovery: SyncResultSuccess) -> None:
        logger.info(
            "Actions discovered",
            num_actions=len(discovery.actions),
            num_validation_errors=len(discovery.validation_errors),
        )

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
