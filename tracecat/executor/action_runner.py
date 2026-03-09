"""Action runner for subprocess-based execution (untrusted mode).

This module provides the ActionRunner class that executes registry actions
in isolated subprocesses with tarball venv caching.

All execution is untrusted - DB credentials are never passed to subprocesses.
Secrets and variables are pre-resolved on the host.

Key features:
- Tarball extraction: Downloads and extracts pre-built venv tarballs from S3
- Caching: Reuses extracted tarballs by cache key for fast subsequent runs
- Subprocess execution: Runs actions via minimal_runner.py
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
from urllib.parse import urlparse

import httpx
import orjson
from pydantic_core import to_json

from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.logger import logger
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.types import ResourceLimits
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
        # Per-cache-key locks to prevent duplicate downloads in async context
        self._extraction_locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()  # Protects _extraction_locks dict
        logger.info("ActionRunner initialized", cache_dir=str(self.cache_dir))

    async def _get_extraction_lock(self, cache_key: str) -> asyncio.Lock:
        """Get or create a lock for the given cache key.

        This prevents multiple concurrent async tasks from downloading
        the same tarball simultaneously.
        """
        async with self._locks_lock:
            if cache_key not in self._extraction_locks:
                self._extraction_locks[cache_key] = asyncio.Lock()
            return self._extraction_locks[cache_key]

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

        Uses per-cache-key locking to prevent duplicate downloads from
        concurrent async tasks. Falls back to atomic rename pattern for
        cross-process coordination (e.g., multiple worker pods).

        Returns the path to the extracted directory (add to PYTHONPATH).
        """
        target_dir = self.cache_dir / f"tarball-{cache_key}"

        # Fast path: already extracted (no lock needed for read check)
        if target_dir.exists():
            logger.debug("Using cached tarball extraction", cache_key=cache_key)
            return target_dir

        # Acquire per-cache-key lock to prevent duplicate downloads
        lock = await self._get_extraction_lock(cache_key)
        async with lock:
            # Double-check after acquiring lock (another task may have finished)
            if target_dir.exists():
                logger.debug(
                    "Tarball extracted by another task while waiting",
                    cache_key=cache_key,
                )
                return target_dir

            logger.info("Downloading and extracting tarball", cache_key=cache_key)
            start_time = time.monotonic()

            # Use PID + unique ID in temp names to avoid conflicts
            # PID handles cross-process conflicts, unique_id handles concurrent async tasks
            unique_id = id(asyncio.current_task())
            temp_tarball = (
                self.cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}.tar.gz"
            )
            temp_dir = self.cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}.tmp"

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

                # Atomic rename - if another process won the race, this fails
                try:
                    temp_dir.rename(target_dir)
                    total_elapsed = (time.monotonic() - start_time) * 1000
                    logger.info(
                        "Tarball extracted and cached",
                        cache_key=cache_key,
                        download_ms=f"{download_elapsed:.1f}",
                        extract_ms=f"{extract_elapsed:.1f}",
                        total_ms=f"{total_elapsed:.1f}",
                    )
                except OSError:
                    # Another process already created target_dir - that's fine
                    if target_dir.exists():
                        logger.debug(
                            "Tarball already extracted by another process",
                            cache_key=cache_key,
                        )
                    else:
                        raise
            finally:
                # Clean up temp files
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
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
        resolved_context: ResolvedContext,
        tarball_uris: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
        force_sandbox: bool = False,
    ) -> ExecutionResult:
        """Execute an action in a subprocess (untrusted mode).

        All execution is in untrusted mode - DB credentials are never passed
        to subprocesses. Secrets and variables are pre-resolved on the host.

        Args:
            input: The RunActionInput containing task and context
            role: The Role for authorization
            tarball_uris: List of S3 URIs to pre-built tarball venvs (deterministic order)
            env_vars: Additional environment variables for the subprocess
            timeout: Execution timeout in seconds
            force_sandbox: If True, always use nsjail sandbox regardless of config
            resolved_context: Pre-resolved context from service layer (optional,
                will be resolved here if not provided)

        Returns:
            The action result, or ExecutorActionErrorInfo on error
        """
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Download and extract each tarball venv, collect paths in deterministic order
        registry_paths: list[Path] = []
        if tarball_uris:
            for tarball_uri in tarball_uris:
                cache_key = self.compute_tarball_cache_key(tarball_uri)
                target_dir = await self.ensure_tarball_extracted(cache_key, tarball_uri)
                registry_paths.append(target_dir)
            logger.info(
                "Using tarball venvs",
                count=len(registry_paths),
            )
        else:
            # No tarballs available - use empty base dir
            base_dir = self.cache_dir / "base"
            base_dir.mkdir(parents=True, exist_ok=True)
            registry_paths = [base_dir]
            logger.info("No tarball URIs provided, using base PYTHONPATH")

        # Check if sandbox execution is enabled and available
        # force_sandbox=True overrides config (used by ephemeral backend)
        use_sandbox = force_sandbox or (
            config.TRACECAT__EXECUTOR_SANDBOX_ENABLED and _is_sandbox_available()
        )
        logger.debug(
            "Using sandbox execution",
            use_sandbox=use_sandbox,
            force_sandbox=force_sandbox,
        )

        if use_sandbox:
            return await self._execute_sandboxed(
                input=input,
                role=role,
                registry_paths=registry_paths,
                env_vars=env_vars,
                timeout=timeout,
                resolved_context=resolved_context,
            )
        else:
            return await self._execute_direct(
                input=input,
                role=role,
                registry_paths=registry_paths,
                env_vars=env_vars,
                timeout=timeout,
                resolved_context=resolved_context,
            )

    async def _execute_sandboxed(
        self,
        input: RunActionInput,
        role: Role,
        registry_paths: list[Path],
        resolved_context: ResolvedContext,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute an action in an nsjail sandbox (untrusted mode).

        All sandbox execution is untrusted - DB credentials are never passed.
        Secrets and variables must be pre-resolved and passed via resolved_context.

        Args:
            input: The RunActionInput containing task and context
            role: The Role for authorization
            registry_paths: List of directories containing extracted registry tarballs
            resolved_context: Pre-resolved secrets, variables, and action impl
            env_vars: Additional environment variables for the subprocess
            timeout: Execution timeout in seconds
        """
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Create temporary directory for file-based IPC
        job_dir = Path(tempfile.mkdtemp(prefix="tracecat_action_"))

        try:
            # Build payload with resolved_context
            payload: dict[str, Any] = {
                "input": input,
                "role": role,
                "resolved_context": resolved_context,
            }

            # Write input JSON to job directory
            input_json = to_json(payload)
            input_path = job_dir / "input.json"
            input_path.write_bytes(input_json)

            # Copy minimal_runner.py to job directory
            from tracecat.executor import minimal_runner as minimal_runner_module

            minimal_runner_src = Path(minimal_runner_module.__file__)
            minimal_runner_dst = job_dir / "minimal_runner.py"
            shutil.copy2(minimal_runner_src, minimal_runner_dst)

            # Build environment variables for sandbox (untrusted mode only)
            # NOTE: DB credentials are intentionally NOT passed
            sandbox_env: dict[str, str] = {}
            if env_vars:
                sandbox_env.update(env_vars)

            # SDK context for any registry SDK operations
            sandbox_env["TRACECAT__API_URL"] = config.TRACECAT__API_URL
            sandbox_env["TRACECAT__WORKSPACE_ID"] = (
                str(role.workspace_id) if role.workspace_id else ""
            )
            sandbox_env["TRACECAT__WORKFLOW_ID"] = str(input.run_context.wf_id)
            sandbox_env["TRACECAT__RUN_ID"] = str(input.run_context.wf_run_id)
            sandbox_env["TRACECAT__WF_EXEC_ID"] = str(input.run_context.wf_exec_id)
            sandbox_env["TRACECAT__ENVIRONMENT"] = input.run_context.environment

            # Mint an executor token for SDK calls
            if role.workspace_id is None:
                raise ValueError("workspace_id is required for sandbox execution")
            executor_token = mint_executor_token(
                workspace_id=role.workspace_id,
                user_id=role.user_id,
                service_id=role.service_id,
                wf_id=str(input.run_context.wf_id),
                wf_exec_id=str(input.run_context.wf_run_id),
            )
            sandbox_env["TRACECAT__EXECUTOR_TOKEN"] = executor_token

            logger.debug(
                "Using untrusted mode - no DB credentials passed to sandbox",
                action=input.task.action,
            )

            # Configure sandbox
            sandbox_config = ActionSandboxConfig(
                registry_paths=registry_paths,
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
                registry_paths_count=len(registry_paths),
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
        registry_paths: list[Path],
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
        resolved_context: ResolvedContext | None = None,
    ) -> ExecutionResult:
        """Execute an action in a direct subprocess (no sandbox)."""
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Prepare input JSON for subprocess
        payload: dict[str, Any] = {"input": input, "role": role}
        if resolved_context is not None:
            payload["resolved_context"] = resolved_context
        input_json = to_json(payload)

        # Build environment with registry paths in PYTHONPATH
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        # Ensure SDK context is available for registry actions executed by minimal_runner.
        if resolved_context is not None:
            env["TRACECAT__API_URL"] = config.TRACECAT__API_URL
            env["TRACECAT__WORKSPACE_ID"] = resolved_context.workspace_id
            env["TRACECAT__WORKFLOW_ID"] = resolved_context.workflow_id
            env["TRACECAT__RUN_ID"] = resolved_context.run_id
            env["TRACECAT__WF_EXEC_ID"] = str(input.run_context.wf_exec_id)
            env["TRACECAT__ENVIRONMENT"] = input.run_context.environment
            env["TRACECAT__EXECUTOR_TOKEN"] = resolved_context.executor_token

        # Build PYTHONPATH with multiple registry paths (deterministic order)
        pythonpath_parts = [str(p) for p in registry_paths if p.exists()]
        existing_pythonpath = env.get("PYTHONPATH", "")
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = ":".join(pythonpath_parts) if pythonpath_parts else ""

        # Get path to minimal_runner.py for subprocess execution
        from tracecat.executor import minimal_runner as minimal_runner_module

        minimal_runner_path = Path(minimal_runner_module.__file__)

        logger.debug(
            "Executing action in subprocess",
            action=input.task.action,
            timeout=timeout,
        )

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(minimal_runner_path),
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
