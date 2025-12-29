"""Action runner for subprocess-based execution.

This module provides the ActionRunner class that executes registry actions
in isolated subprocesses with tarball venv caching.

Key features:
- Tarball extraction: Downloads and extracts pre-built venv tarballs from S3
- Caching: Reuses extracted tarballs by cache key for fast subsequent runs
- Subprocess execution: Runs actions via subprocess_entrypoint.py
- nsjail sandboxing: Optional OS-level isolation with resource limits
- Timeout handling: Kills subprocess on timeout
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import orjson
from pydantic_core import to_json

from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.auth.types import AccessLevel
from tracecat.auth.types import Role as AuthRole
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ResolvedContext,
    get_trust_mode,
)
from tracecat.expressions.eval import collect_expressions
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.types import ResourceLimits
from tracecat.secrets import secrets_manager
from tracecat.storage import blob

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput

type ExecutionResult = Any | ExecutorActionErrorInfo


def _get_tracecat_app_dir() -> Path:
    """Get the tracecat package directory for sandbox mounting.

    Returns the directory containing the tracecat package, which will be
    mounted read-only in the sandbox at /app.
    """
    # Check if explicitly configured
    if config.TRACECAT__EXECUTOR_TRACECAT_APP_DIR:
        return Path(config.TRACECAT__EXECUTOR_TRACECAT_APP_DIR)

    # Auto-detect from installed package location
    import tracecat

    tracecat_path = Path(tracecat.__file__).parent
    # Return parent (site-packages) so sandbox can import tracecat
    return tracecat_path.parent


def _get_site_packages_dir() -> Path | None:
    """Get the site-packages directory containing Python dependencies.

    Returns the site-packages directory where tracecat's dependencies
    (orjson, pydantic, sqlalchemy, etc.) are installed.
    """
    # Check if explicitly configured
    if config.TRACECAT__EXECUTOR_SITE_PACKAGES_DIR:
        return Path(config.TRACECAT__EXECUTOR_SITE_PACKAGES_DIR)

    # Auto-detect using sysconfig
    import sysconfig

    site_packages = sysconfig.get_path("purelib")
    if site_packages:
        return Path(site_packages)
    return None


def _is_sandbox_available() -> bool:
    """Check if nsjail sandbox is available."""
    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)

    if not nsjail_path.exists():
        logger.debug("nsjail binary not found", path=str(nsjail_path))
        return False

    if not rootfs_path.exists():
        logger.debug("Sandbox rootfs not found", path=str(rootfs_path))
        return False

    return True


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse an s3://bucket/key URI into (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    rest = uri.removeprefix("s3://")
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return bucket, key


class ActionRunner:
    """Runs registry actions in subprocesses with tarball venv caching.

    This runner:
    1. Computes a cache key from the tarball URI
    2. Downloads and extracts tarballs to a cached target directory
    3. Executes the action in a subprocess with PYTHONPATH set
    4. Returns the result or error
    """

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or Path(config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR)
        self._install_locks: dict[str, asyncio.Lock] = {}
        logger.info("ActionRunner initialized", cache_dir=str(self.cache_dir))

    def _get_install_lock(self, cache_key: str) -> asyncio.Lock:
        """Get or create a lock for a specific cache key."""
        if cache_key not in self._install_locks:
            self._install_locks[cache_key] = asyncio.Lock()
        return self._install_locks[cache_key]

    async def _tarball_uri_to_http_url(self, s3_uri: str) -> str:
        """Convert S3 URI to presigned HTTP URL for tarball download."""
        bucket, key = _parse_s3_uri(s3_uri)
        url = await blob.generate_presigned_download_url(
            key=key,
            bucket=bucket,
            expiry=3600,
            force_download=False,
        )
        logger.debug("Generated presigned URL for tarball", s3_uri=s3_uri)
        return url

    def compute_tarball_cache_key(self, tarball_uri: str) -> str:
        """Compute cache key from tarball URI."""
        if not tarball_uri:
            return "base"
        # Don't lowercase - S3 keys are case-sensitive, lowercasing could cause collisions
        content = tarball_uri.strip()
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def ensure_registry_environment(self, tarball_uri: str | None) -> Path | None:
        """Ensure the registry environment is set up and return the PYTHONPATH.

        This is the public API for pool workers to get the path to add to PYTHONPATH.

        Args:
            tarball_uri: S3 URI to the pre-built tarball venv.

        Returns:
            Path to add to PYTHONPATH, or None if no tarball available.
        """
        if not tarball_uri:
            return None
        cache_key = self.compute_tarball_cache_key(tarball_uri)
        return await self.ensure_tarball_extracted(cache_key, tarball_uri)

    async def ensure_tarball_extracted(self, cache_key: str, tarball_uri: str) -> Path:
        """Ensure tarball is extracted to a target directory.

        Uses atomic rename pattern to prevent race conditions.
        Downloads and extracts tarball only if not cached.
        Returns the path to the extracted directory (add to PYTHONPATH).
        """
        target_dir = self.cache_dir / f"tarball-{cache_key}"

        # Fast path: already extracted - no S3 calls needed
        if target_dir.exists():
            logger.debug("Using cached tarball extraction", cache_key=cache_key)
            return target_dir

        # Slow path: download and extract under lock
        async with self._get_install_lock(cache_key):
            if target_dir.exists():
                return target_dir

            logger.info("Downloading and extracting tarball", cache_key=cache_key)
            start_time = time.monotonic()

            # Download tarball to a temporary file
            temp_tarball = self.cache_dir / f"{cache_key}.{os.getpid()}.tar.gz"
            temp_dir = self.cache_dir / f"{cache_key}.{os.getpid()}.tmp"

            try:
                # Create cache dir if needed
                self.cache_dir.mkdir(parents=True, exist_ok=True)

                # Download tarball
                download_start = time.monotonic()
                http_url = await self._tarball_uri_to_http_url(tarball_uri)
                await self._download_file(http_url, temp_tarball)
                download_elapsed = (time.monotonic() - download_start) * 1000

                # Extract tarball
                extract_start = time.monotonic()
                temp_dir.mkdir(parents=True, exist_ok=True)
                await self._extract_tarball(temp_tarball, temp_dir)
                extract_elapsed = (time.monotonic() - extract_start) * 1000

                # Atomic rename
                temp_dir.rename(target_dir)

                total_elapsed = (time.monotonic() - start_time) * 1000
                logger.info(
                    "Tarball extracted and cached",
                    cache_key=cache_key,
                    download_ms=f"{download_elapsed:.1f}",
                    extract_ms=f"{extract_elapsed:.1f}",
                    total_ms=f"{total_elapsed:.1f}",
                )
            except Exception:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                if temp_tarball.exists():
                    temp_tarball.unlink(missing_ok=True)
                raise
            finally:
                # Clean up temp tarball
                if temp_tarball.exists():
                    temp_tarball.unlink(missing_ok=True)

            return target_dir

    async def _download_file(self, url: str, output_path: Path) -> None:
        """Download a file from HTTP URL to local path."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)

        # Log only the path portion to avoid leaking presigned URL signatures
        from urllib.parse import urlparse

        parsed = urlparse(url)
        safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        logger.debug(
            "File downloaded",
            url=safe_url[:80],
            size_bytes=output_path.stat().st_size,
        )

    async def _extract_tarball(self, tarball_path: Path, target_dir: Path) -> None:
        """Extract a gzipped tarball to target directory."""

        def _do_extract() -> None:
            with tarfile.open(tarball_path, "r:gz") as tar:
                # Extract all contents to target directory
                # Use filter='data' to prevent path traversal attacks (CVE-2007-4559)
                tar.extractall(path=target_dir, filter="data")

        await asyncio.to_thread(_do_extract)
        logger.debug("Tarball extracted", target=str(target_dir))

    async def execute_action(
        self,
        input: RunActionInput,
        role: Role,
        tarball_uri: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
        force_sandbox: bool = False,
        trust_mode: str | None = None,
    ) -> ExecutionResult:
        """Execute an action in a subprocess.

        Args:
            input: The RunActionInput containing task and context
            role: The Role for authorization
            tarball_uri: S3 URI to pre-built tarball venv
            env_vars: Additional environment variables for the subprocess
            timeout: Execution timeout in seconds
            force_sandbox: If True, always use nsjail sandbox regardless of config
            trust_mode: Override trust mode ('trusted' or 'untrusted').
                If None, derives from executor backend type.

        Returns:
            The action result, or ExecutorActionErrorInfo on error
        """

        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT
        trust_mode = trust_mode or get_trust_mode()

        # Download and extract tarball venv
        if tarball_uri:
            cache_key = self.compute_tarball_cache_key(tarball_uri)
            target_dir = await self.ensure_tarball_extracted(cache_key, tarball_uri)
            logger.info(
                "Using tarball venv",
                cache_key=cache_key,
                tarball_uri=tarball_uri[:80],
            )
        else:
            # No tarball available - use empty cache dir
            target_dir = self.cache_dir / "base"
            target_dir.mkdir(parents=True, exist_ok=True)
            logger.info("No tarball URI provided, using base PYTHONPATH")

        # Check if sandbox execution is enabled and available
        # force_sandbox=True overrides config (used by ephemeral backend)
        use_sandbox = force_sandbox or (
            config.TRACECAT__EXECUTOR_SANDBOX_ENABLED and _is_sandbox_available()
        )
        logger.debug(
            "Using sandbox execution",
            use_sandbox=use_sandbox,
            force_sandbox=force_sandbox,
            trust_mode=trust_mode,
        )

        # For untrusted mode, pre-resolve secrets and variables
        resolved_context: ResolvedContext | None = None
        if trust_mode == "untrusted":
            resolved_context = await self._prepare_resolved_context(input, role)

        if use_sandbox:
            return await self._execute_sandboxed(
                input=input,
                role=role,
                registry_cache_dir=target_dir,
                env_vars=env_vars,
                timeout=timeout,
                trust_mode=trust_mode,
                resolved_context=resolved_context,
            )
        else:
            return await self._execute_direct(
                input=input,
                role=role,
                registry_cache_dir=target_dir,
                env_vars=env_vars,
                timeout=timeout,
            )

    async def _prepare_resolved_context(
        self,
        input: RunActionInput,
        role: Role,
    ) -> ResolvedContext:
        """Pre-resolve secrets and variables for untrusted mode.

        In untrusted mode, the sandbox doesn't have DB access, so we
        resolve secrets and variables here and pass them separately.
        """
        # Lazy import to avoid circular dependency with service.py
        from tracecat.executor.service import get_workspace_variables

        task = input.task
        action_name = task.action

        # Get action secrets configuration
        async with RegistryActionsService.with_session() as service:
            reg_action = await service.get_action(action_name)
            action_secrets = await service.fetch_all_action_secrets(reg_action)

        # Collect expression references from task args
        collected = collect_expressions(task.args)

        # Resolve secrets
        secrets = await secrets_manager.get_action_secrets(
            secret_exprs=collected.secrets, action_secrets=action_secrets
        )

        # Resolve workspace variables
        workspace_variables = await get_workspace_variables(
            variable_exprs=collected.variables,
            environment=input.run_context.environment,
            role=role,
        )

        logger.debug(
            "Pre-resolved secrets and variables for untrusted mode",
            action=action_name,
            num_secrets=len(secrets),
            num_variables=len(workspace_variables),
        )

        return ResolvedContext(secrets=secrets, variables=workspace_variables)

    async def _execute_sandboxed(
        self,
        input: RunActionInput,
        role: Role,
        registry_cache_dir: Path,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
        trust_mode: str = "trusted",
        resolved_context: ResolvedContext | None = None,
    ) -> ExecutionResult:
        """Execute an action in an nsjail sandbox.

        Args:
            input: The RunActionInput containing task and context
            role: The Role for authorization
            registry_cache_dir: Directory containing extracted registry tarballs
            env_vars: Additional environment variables for the subprocess
            timeout: Execution timeout in seconds
            trust_mode: 'trusted' (pass DB creds) or 'untrusted' (SDK mode)
            resolved_context: Pre-resolved secrets/variables for untrusted mode
        """
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Create temporary directory for file-based IPC
        job_dir = Path(tempfile.mkdtemp(prefix="tracecat_action_"))

        try:
            # Build payload with optional resolved_context for untrusted mode
            payload: dict[str, Any] = {"input": input, "role": role}
            if resolved_context is not None:
                payload["resolved_context"] = resolved_context

            # Write input JSON to job directory
            input_json = to_json(payload)
            input_path = job_dir / "input.json"
            input_path.write_bytes(input_json)

            # Build environment variables for sandbox based on trust mode
            sandbox_env: dict[str, str] = {}
            if env_vars:
                sandbox_env.update(env_vars)

            # Set trust mode so subprocess_entrypoint knows how to run
            sandbox_env["TRACECAT__EXECUTOR_TRUST_MODE"] = trust_mode

            if trust_mode == "untrusted":
                # Untrusted mode: Pass SDK context, NOT DB credentials
                sandbox_env["TRACECAT__API_URL"] = config.TRACECAT__API_URL
                sandbox_env["TRACECAT__WORKSPACE_ID"] = (
                    str(role.workspace_id) if role.workspace_id else ""
                )
                sandbox_env["TRACECAT__WORKFLOW_ID"] = str(input.run_context.wf_id)
                sandbox_env["TRACECAT__RUN_ID"] = str(input.run_context.wf_run_id)
                sandbox_env["TRACECAT__ENVIRONMENT"] = input.run_context.environment

                # Mint an executor token for SDK calls
                if is_feature_enabled(FeatureFlag.EXECUTOR_AUTH):
                    executor_role = AuthRole(
                        type="service",
                        service_id="tracecat-executor",
                        access_level=AccessLevel.ADMIN,
                        workspace_id=role.workspace_id,
                        organization_id=role.organization_id,
                        user_id=role.user_id,
                    )
                    executor_token = mint_executor_token(
                        role=executor_role,
                        run_id=str(input.run_context.wf_run_id),
                        workflow_id=str(input.run_context.wf_id),
                    )
                    sandbox_env["TRACECAT__EXECUTOR_TOKEN"] = executor_token

                logger.debug(
                    "Using untrusted mode - no DB credentials passed to sandbox",
                    action=input.task.action,
                )
            else:
                # Trusted mode: Pass DB credentials for direct access
                for var in [
                    "TRACECAT__DB_URI",
                    "TRACECAT__DB_USER",
                    "TRACECAT__DB_PASS",
                    "TRACECAT__DB_ENDPOINT",
                    "TRACECAT__DB_PORT",
                    "TRACECAT__DB_NAME",
                    "TRACECAT__DB_SSLMODE",
                    "TRACECAT__DB_ENCRYPTION_KEY",
                    "TRACECAT__BLOB_STORAGE_PROTOCOL",
                    "TRACECAT__BLOB_STORAGE_ENDPOINT",
                    "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY",
                    "MINIO_ROOT_USER",
                    "MINIO_ROOT_PASSWORD",
                ]:
                    if var in os.environ:
                        sandbox_env[var] = os.environ[var]

            # Configure sandbox
            sandbox_config = ActionSandboxConfig(
                registry_cache_dir=registry_cache_dir,
                tracecat_app_dir=_get_tracecat_app_dir(),
                site_packages_dir=_get_site_packages_dir(),
                env_vars=sandbox_env,
                resources=ResourceLimits(
                    memory_mb=config.TRACECAT__SANDBOX_DEFAULT_MEMORY_MB,
                    timeout_seconds=int(timeout),
                ),
                timeout_seconds=timeout,
            )

            logger.debug(
                "Executing action in nsjail sandbox",
                action=input.task.action,
                job_dir=str(job_dir),
                registry_cache=str(registry_cache_dir),
            )

            # Execute in sandbox
            start_time = time.monotonic()
            executor = NsjailExecutor()
            result = await executor.execute_action(job_dir, sandbox_config)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "Sandbox execution completed",
                action=input.task.action,
                elapsed_ms=f"{elapsed_ms:.1f}",
                success=result.success,
                exit_code=result.exit_code,
            )

            # Process result
            if result.success:
                return result.output

            # Handle error from sandbox
            if result.error:
                # Try to parse as ExecutorActionErrorInfo
                if isinstance(result.error, dict):
                    return ExecutorActionErrorInfo.model_validate(result.error)
                return ExecutorActionErrorInfo(
                    type="SandboxError",
                    message=str(result.error),
                    action_name=input.task.action,
                    filename="<sandbox>",
                    function="execute_action",
                )

            return ExecutorActionErrorInfo(
                type="SandboxError",
                message=f"Sandbox execution failed with exit code {result.exit_code}",
                action_name=input.task.action,
                filename="<sandbox>",
                function="execute_action",
            )

        finally:
            # Cleanup job directory
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _execute_direct(
        self,
        input: RunActionInput,
        role: Role,
        registry_cache_dir: Path,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute an action in a direct subprocess (no sandbox)."""
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Prepare input JSON for subprocess
        input_json = to_json({"input": input, "role": role})

        # Build environment with target directory in PYTHONPATH
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        existing_pythonpath = env.get("PYTHONPATH", "")
        if existing_pythonpath:
            env["PYTHONPATH"] = f"{registry_cache_dir}:{existing_pythonpath}"
        else:
            env["PYTHONPATH"] = str(registry_cache_dir)

        logger.debug(
            "Executing action in subprocess",
            action=input.task.action,
            timeout=timeout,
        )

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tracecat.executor.subprocess_entrypoint",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_json),
                timeout=timeout,
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "Subprocess execution completed",
                action=input.task.action,
                elapsed_ms=f"{elapsed_ms:.1f}",
                returncode=proc.returncode,
            )
        except TimeoutError:
            logger.error(
                "Action execution timed out, killing subprocess",
                action=input.task.action,
                timeout=timeout,
            )
            proc.kill()
            await proc.wait()
            return ExecutorActionErrorInfo(
                type="TimeoutError",
                message=f"Action execution timed out after {timeout}s",
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        # Check for subprocess crash
        if proc.returncode != 0:
            stderr_text = stderr.decode()
            logger.error(
                "Subprocess failed",
                action=input.task.action,
                returncode=proc.returncode,
                stderr=stderr_text,
            )
            return ExecutorActionErrorInfo(
                type="SubprocessError",
                message=f"Subprocess exited with code {proc.returncode}: {stderr_text[:500]}",
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        # Parse result from stdout
        try:
            result_data = orjson.loads(stdout)
        except orjson.JSONDecodeError as e:
            logger.error(
                "Failed to parse subprocess output",
                action=input.task.action,
                stdout=stdout.decode()[:500],
                error=str(e),
            )
            return ExecutorActionErrorInfo(
                type="ProtocolError",
                message=f"Failed to parse subprocess output: {e}",
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        # Handle success or error
        if result_data.get("success"):
            return result_data["result"]

        # Reconstruct error info
        error_data = result_data.get("error")
        if error_data:
            return ExecutorActionErrorInfo.model_validate(error_data)

        return ExecutorActionErrorInfo(
            type="UnknownError",
            message="Subprocess returned neither success nor error",
            action_name=input.task.action,
            filename="<subprocess>",
            function="execute_action",
        )


# Lazy singleton - no lifespan required
_action_runner: ActionRunner | None = None


def get_action_runner() -> ActionRunner:
    """Get the global ActionRunner instance (lazy initialized)."""
    global _action_runner
    if _action_runner is None:
        _action_runner = ActionRunner()
    return _action_runner
