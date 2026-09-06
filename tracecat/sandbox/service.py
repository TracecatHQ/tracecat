"""High-level sandbox service for Python script execution."""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tracecat.config import (
    TRACECAT__DISABLE_NSJAIL,
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_DEFAULT_MEMORY_MB,
    TRACECAT__SANDBOX_DEFAULT_TIMEOUT,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_PACKAGE_CACHE_MAX_BYTES,
    TRACECAT__SANDBOX_PACKAGE_CACHE_MAX_ENTRIES,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger
from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxExecutionError,
    SandboxFileSafetyError,
    SandboxInfrastructureError,
    raise_for_sandbox_error_code,
)
from tracecat.sandbox.executor import RUN_PYTHON_ACTION_GATEWAY_SOCKET, NsjailExecutor
from tracecat.sandbox.file_io import copy_tree_without_following_symlinks
from tracecat.sandbox.types import (
    ResourceLimits,
    SandboxConfig,
    SandboxErrorCode,
    SandboxNetworkPurpose,
    SandboxNetworkRequest,
)
from tracecat.sandbox.unsafe_pid_executor import UnsafePidExecutor
from tracecat.sandbox.wrapper import INSTALL_SCRIPT, WRAPPER_SCRIPT


async def _await_task_rejoined(task: asyncio.Task[Any]) -> Any:
    """Await a worker task, rejoining its thread even under cancellation.

    Cancelling an ``asyncio.to_thread`` await does not stop the worker thread;
    a caller that proceeds to more filesystem work (cleanup removals,
    caller-side job-dir removal) would otherwise race the still-running
    thread. If cancellation arrives during the wait, the thread is joined
    (further cancellation attempts are swallowed) and the cancellation is
    re-raised afterwards — a cancelled caller must not silently fall through
    to further execution (e.g. script execution after cleanup), and the
    worker's outcome (result or error) is deliberately discarded so the
    pending cancellation is never masked. Otherwise the task's result is
    returned (or its exception re-raised).
    """
    done = asyncio.Event()
    task.add_done_callback(lambda _task: done.set())
    try:
        await done.wait()
    except asyncio.CancelledError:
        # The worker thread keeps running; join it fully (swallowing further
        # cancellation attempts) before surfacing the cancellation so no
        # cleanup can race the in-flight worker.
        while not done.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await done.wait()
        # Retrieve the worker outcome so it does not linger as an
        # unretrieved-exception; the pending cancellation supersedes it.
        worker_outcome = task.exception()
        raise asyncio.CancelledError() from worker_outcome
    return task.result()


def validate_run_python_script(script: str) -> tuple[bool, str | None]:
    """Validate that a Python script has the required structure for run_python.

    The script must contain at least one function definition. If multiple
    functions are defined, one must be named 'main'.

    Args:
        script: The Python script content to validate.

    Returns:
        A tuple of (is_valid, error_message). If valid, error_message is None.
    """
    function_pattern = r"(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
    functions = re.findall(function_pattern, script)

    if not functions:
        return False, "Script must contain at least one function definition."

    if len(functions) > 1 and "main" not in functions:
        return (
            False,
            "When script contains multiple functions, one must be named 'main'.",
        )

    return True, None


class SandboxService:
    """High-level service for executing Python scripts in an nsjail sandbox.

    This service handles the two-phase execution model:
    1. Package Installation Phase (if dependencies specified)
       - Network access enabled for downloading packages
       - Packages installed to a cached location
    2. Script Execution Phase
       - Network access configurable
       - Cached packages mounted read-only
       - User script executed with inputs
    """

    def __init__(
        self,
        cache_dir: str = TRACECAT__SANDBOX_CACHE_DIR,
    ):
        self.cache_dir = Path(cache_dir)
        self.package_cache = self.cache_dir / "packages"

        # Ensure cache directories exist
        self.package_cache.mkdir(parents=True, exist_ok=True)

        # Initialize executors lazily based on availability
        self._nsjail_executor: NsjailExecutor | None = None
        self._unsafe_pid_executor: UnsafePidExecutor | None = None

    def _is_nsjail_available(self) -> bool:
        """Check if nsjail sandbox is available and configured.

        Returns:
            True if nsjail can be used, False otherwise.
        """
        if TRACECAT__DISABLE_NSJAIL:
            return False

        nsjail_path = Path(TRACECAT__SANDBOX_NSJAIL_PATH)
        rootfs_path = Path(TRACECAT__SANDBOX_ROOTFS_PATH)

        return nsjail_path.exists() and rootfs_path.is_dir()

    @property
    def nsjail_executor(self) -> NsjailExecutor:
        """Get the nsjail executor, creating it if needed."""
        if self._nsjail_executor is None:
            self._nsjail_executor = NsjailExecutor()
        return self._nsjail_executor

    @property
    def unsafe_pid_executor(self) -> UnsafePidExecutor:
        """Get the unsafe PID executor, creating it if needed."""
        if self._unsafe_pid_executor is None:
            self._unsafe_pid_executor = UnsafePidExecutor(cache_dir=str(self.cache_dir))
        return self._unsafe_pid_executor

    @staticmethod
    def _with_action_gateway_socket_env(
        env_vars: dict[str, str] | None,
        *,
        socket_path: Path | None,
        env_socket_path: Path | None = None,
    ) -> dict[str, str] | None:
        """Return env vars with Action Gateway socket routing when configured."""
        resolved_socket_path = SandboxService._require_action_gateway_socket(
            socket_path
        )
        if resolved_socket_path is None:
            return env_vars

        updated = dict(env_vars or {})
        updated["TRACECAT__ACTION_GATEWAY_SOCKET"] = str(
            env_socket_path or resolved_socket_path
        )
        return updated

    @staticmethod
    def _require_action_gateway_socket(socket_path: Path | None) -> Path | None:
        """Return a configured Action Gateway socket or fail if it is unavailable."""
        if socket_path is None:
            return None
        if not socket_path.exists():
            raise SandboxInfrastructureError(
                f"Action Gateway socket is unavailable: {socket_path}"
            )
        return socket_path

    @asynccontextmanager
    async def _create_job_dir(self) -> AsyncIterator[Path]:
        """Create a temporary job directory for script execution.

        Yields:
            Path to the temporary job directory.
        """
        temp_dir = tempfile.mkdtemp(prefix="sandbox-job-")
        job_dir = Path(temp_dir)
        try:
            yield job_dir
        finally:
            # Clean up job directory
            shutil.rmtree(job_dir, ignore_errors=True)

    def _compute_cache_key(
        self, dependencies: list[str], workspace_id: str | None = None
    ) -> str:
        """Compute a cache key from dependencies and optional workspace ID.

        The key is a SHA256 hash of the sorted, normalized dependencies,
        optionally scoped to a workspace for multi-tenant isolation.

        Args:
            dependencies: List of package specifications.
            workspace_id: Optional workspace ID for multi-tenant cache isolation.
                When provided, packages installed by one workspace cannot be
                reused by another, preventing potential supply chain attacks.

        Returns:
            16-character hexadecimal cache key.
        """
        normalized = sorted(dep.lower().strip() for dep in dependencies)
        # Include workspace ID in hash for multi-tenant isolation
        # This prevents cross-workspace package cache poisoning
        if workspace_id:
            hash_input = f"{workspace_id}\n" + "\n".join(normalized)
        else:
            hash_input = "\n".join(normalized)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    async def _install_packages(
        self,
        job_dir: Path,
        dependencies: list[str],
        cache_key: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Install packages inside the sandbox.

        This is Phase 1 of the two-phase execution model.

        Args:
            job_dir: Temporary job directory.
            dependencies: List of packages to install.
            cache_key: Cache key for storing installed packages.
            timeout_seconds: Maximum installation time.

        Raises:
            PackageInstallError: If package installation fails.
        """
        # Create cache dir in job_dir
        cache_dir = job_dir / "cache"
        cache_dir.mkdir(exist_ok=True)
        (cache_dir / "site-packages").mkdir(exist_ok=True)

        # Write dependencies to secure JSON file (prevents code injection)
        # SECURITY: Never interpolate user-provided dependency strings into Python code
        deps_path = job_dir / "dependencies.json"
        deps_path.write_text(json.dumps(dependencies))
        deps_path.chmod(0o600)

        # Write static install script
        install_path = job_dir / "install.py"
        install_path.write_text(INSTALL_SCRIPT)
        install_path.chmod(0o600)

        logger.info(
            "Installing packages",
            dependencies=dependencies,
            cache_key=cache_key,
        )

        # Run installation with network enabled
        result = await self.nsjail_executor.execute_install(
            job_dir, cache_key, timeout_seconds
        )

        if not result.success:
            logger.error(
                "Package installation failed",
                dependencies=dependencies,
                error=result.error,
                stderr=result.stderr[:500],
            )
            if result.error_code is SandboxErrorCode.INFRASTRUCTURE_FAILURE:
                raise_for_sandbox_error_code(
                    result.error_code,
                    "Package installation sandbox failed before starting",
                )
            raise PackageInstallError(
                f"Failed to install packages: {result.error or 'Unknown error'}"
            )

        # Promotion safety invariant: execute_install() has already awaited and
        # reaped the install jail (exit_code is its final return code), so the
        # sandbox-controlled tree cannot change between validation and copy.
        if result.exit_code is None:
            raise PackageInstallError(
                "Package install jail did not report a completed execution"
            )

        # Copy installed packages to shared cache using atomic rename.
        # This prevents race conditions when multiple concurrent requests
        # try to install the same dependencies.
        site_packages = cache_dir / "site-packages"
        dest = self.package_cache / cache_key / "site-packages"
        dest.parent.mkdir(parents=True, exist_ok=True)

        # The installer has stopped, so validate the tree and preserve symlinks
        # rather than dereferencing sandbox-selected targets in the host process.
        temp_dest = dest.parent / f"site-packages.{uuid.uuid4().hex}.tmp"
        copy_task = asyncio.create_task(
            asyncio.to_thread(
                copy_tree_without_following_symlinks,
                site_packages,
                temp_dest,
                trusted_root=job_dir,
                max_bytes=TRACECAT__SANDBOX_PACKAGE_CACHE_MAX_BYTES,
                max_entries=TRACECAT__SANDBOX_PACKAGE_CACHE_MAX_ENTRIES,
            )
        )
        try:
            try:
                # The walk and copy can span gigabytes; keep them off the loop.
                # Shielded so activity cancellation cannot abandon the copy
                # thread mid-flight: cancelling the await does not stop the
                # thread, and a still-running copy would race the finally-block
                # removal of temp_dest and the caller's job-dir cleanup.
                copied = await asyncio.shield(copy_task)
            except asyncio.CancelledError:
                # Rejoin the copy thread (shielded even under repeated
                # cancellation) before any cleanup runs. Prefer the pending
                # cancellation if the copy also failed; the temp tree is
                # removed in the finally block.
                try:
                    await _await_task_rejoined(copy_task)
                except SandboxFileSafetyError as exc:
                    logger.warning(
                        "Discarded cancelled package promotion",
                        error=type(exc).__name__,
                    )
                except Exception as exc:
                    # A copy-worker failure that already completed before the
                    # join (e.g. PermissionError or shutil.Error) must not
                    # mask the pending cancellation either; log it and let the
                    # cancellation win once the thread is rejoined. Log the
                    # exception class only: str(exc) can embed sandbox-
                    # controlled filenames and paths.
                    logger.warning(
                        "Cancelled package promotion copy worker failed",
                        error=type(exc).__name__,
                    )
                raise
            except SandboxFileSafetyError as exc:
                raise PackageInstallError(
                    "Installed packages contained unsafe filesystem entries"
                ) from exc

            if not copied:
                logger.warning(
                    "No packages were installed",
                    dependencies=dependencies,
                )
                return

            # Atomic rename into place. If another installer won the race, use
            # the already-published cache tree.
            try:
                os.rename(temp_dest, dest)
                logger.info(
                    "Packages cached",
                    cache_key=cache_key,
                    path=str(dest),
                )
            except OSError:
                logger.debug(
                    "Cache already exists (concurrent install), using existing",
                    cache_key=cache_key,
                )
        finally:
            # The copy thread is always joined before this point, but the
            # removal itself must also complete under cancellation (a stranded
            # partial .tmp tree in the shared package cache persists forever).
            # If cancellation arrives mid-removal, the rejoined helper
            # re-raises it after the tree is gone so the cancelled activity
            # does not continue into script execution.
            if temp_dest.exists():
                rmtree_task = asyncio.create_task(
                    asyncio.to_thread(
                        shutil.rmtree,
                        temp_dest,
                        ignore_errors=True,
                    )
                )
                await _await_task_rejoined(rmtree_task)

    async def _prepare_execution(
        self,
        job_dir: Path,
        script: str,
        inputs: dict[str, Any] | None,
    ) -> None:
        """Prepare the job directory for script execution.

        Args:
            job_dir: Temporary job directory.
            script: User's Python script content.
            inputs: Input data for the script.
        """
        # Write user script
        script_path = job_dir / "script.py"
        script_path.write_text(script)
        script_path.chmod(0o600)

        # Write inputs
        inputs_path = job_dir / "inputs.json"
        inputs_path.write_text(json.dumps(inputs or {}))
        inputs_path.chmod(0o600)

        # Write wrapper script
        wrapper_path = job_dir / "wrapper.py"
        wrapper_path.write_text(WRAPPER_SCRIPT)
        wrapper_path.chmod(0o600)

    async def run_python(
        self,
        script: str,
        inputs: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        timeout_seconds: int | None = None,
        allow_network: bool = False,
        env_vars: dict[str, str] | None = None,
        python_path_dirs: list[Path] | None = None,
        workspace_id: str | None = None,
        action_gateway_socket: Path | None = None,
    ) -> Any:
        """Execute a Python script in a sandbox.

        This is the main entry point for script execution. It automatically
        selects the appropriate executor based on nsjail availability:
        - If nsjail is available: Uses full OS-level isolation
        - If nsjail is unavailable: Uses fallback executor with PID isolation

        Args:
            script: Python script content to execute.
            inputs: Dictionary of inputs passed to the main function.
            dependencies: List of pip packages to install.
            timeout_seconds: Maximum execution time (default from config).
            allow_network: Whether to allow network access during execution.
                Note: Without nsjail, this is best-effort and not OS-enforced.
            env_vars: Environment variables to set in the sandbox.
            python_path_dirs: Read-only Python path roots to expose to the sandbox.
            workspace_id: Optional workspace ID for multi-tenant cache isolation.
                When provided, package caches are scoped to the workspace,
                preventing cross-workspace package poisoning attacks.
            action_gateway_socket: Optional host-side action gateway Unix socket.
                Internal Tracecat SDK calls use this socket even when outbound
                network access is disabled.

        Returns:
            The return value of the script's main function.

        Raises:
            SandboxExecutionError: If script execution fails.
            PackageInstallError: If package installation fails.
            SandboxTimeoutError: If execution times out.
        """
        if timeout_seconds is None:
            timeout_seconds = TRACECAT__SANDBOX_DEFAULT_TIMEOUT
        resolved_action_gateway_socket = self._require_action_gateway_socket(
            action_gateway_socket
        )

        # Route to appropriate executor based on nsjail availability
        if self._is_nsjail_available():
            logger.debug("Using nsjail executor for script execution")
            return await self._run_with_nsjail(
                script=script,
                inputs=inputs,
                dependencies=dependencies,
                timeout_seconds=timeout_seconds,
                allow_network=allow_network,
                env_vars=env_vars,
                python_path_dirs=python_path_dirs,
                workspace_id=workspace_id,
                action_gateway_socket=resolved_action_gateway_socket,
            )
        else:
            logger.info(
                "nsjail not available, using unsafe PID executor. "
                "Using PID namespace isolation when available. "
                "For full OS-level isolation, set TRACECAT__DISABLE_NSJAIL=false "
                "and ensure nsjail is installed with the sandbox rootfs."
            )
            resolved_env_vars = self._with_action_gateway_socket_env(
                env_vars,
                socket_path=resolved_action_gateway_socket,
            )
            result = await self.unsafe_pid_executor.execute(
                script=script,
                inputs=inputs,
                dependencies=dependencies,
                timeout_seconds=timeout_seconds,
                allow_network=allow_network,
                env_vars=resolved_env_vars,
                python_path_dirs=python_path_dirs,
                workspace_id=workspace_id,
            )

            if not result.success:
                error_msg = result.error or "Unknown error"
                logger.error(
                    "Script execution failed (unsafe PID executor)",
                    error=error_msg,
                    stdout=result.stdout[:500] if result.stdout else None,
                    stderr=result.stderr[:500] if result.stderr else None,
                )
                # Same trust boundary as the nsjail path: a rejected result
                # envelope was produced by sandbox-controlled code and must
                # classify as a workload failure, not a retryable fault.
                message = (
                    error_msg
                    if isinstance(error_msg, str)
                    else "Sandbox execution failed"
                )
                raise_for_sandbox_error_code(result.error_code, message)
                raise SandboxExecutionError(message)

            return result.output

    async def _run_with_nsjail(
        self,
        script: str,
        inputs: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        timeout_seconds: int | None = None,
        allow_network: bool = False,
        env_vars: dict[str, str] | None = None,
        python_path_dirs: list[Path] | None = None,
        workspace_id: str | None = None,
        action_gateway_socket: Path | None = None,
    ) -> Any:
        """Execute a Python script using the nsjail sandbox.

        This method provides full OS-level isolation including:
        - Network isolation
        - Filesystem isolation
        - Process isolation
        - Resource limits (memory, CPU)

        Args:
            script: Python script content to execute.
            inputs: Dictionary of inputs passed to the main function.
            dependencies: List of pip packages to install.
            timeout_seconds: Maximum execution time.
            allow_network: Whether to allow network access during execution.
            env_vars: Environment variables to set in the sandbox.
            python_path_dirs: Read-only Python path roots to expose to the sandbox.
            workspace_id: Optional workspace ID for multi-tenant cache isolation.
            action_gateway_socket: Optional host-side action gateway Unix socket.

        Returns:
            The return value of the script's main function.

        Raises:
            SandboxExecutionError: If script execution fails.
            PackageInstallError: If package installation fails.
            SandboxTimeoutError: If execution times out.
        """
        if timeout_seconds is None:
            timeout_seconds = TRACECAT__SANDBOX_DEFAULT_TIMEOUT
        resolved_action_gateway_socket = self._require_action_gateway_socket(
            action_gateway_socket
        )

        async with self._create_job_dir() as job_dir:
            cache_key = None

            # Phase 1: Install packages if needed
            if dependencies:
                cache_key = self._compute_cache_key(dependencies, workspace_id)
                cached_path = self.package_cache / cache_key / "site-packages"

                if not cached_path.exists():
                    logger.info(
                        "Cache miss, installing packages",
                        cache_key=cache_key,
                        dependencies=dependencies,
                    )
                    await self._install_packages(
                        job_dir,
                        dependencies,
                        cache_key,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    logger.debug(
                        "Cache hit, using cached packages",
                        cache_key=cache_key,
                    )

            # Phase 2: Execute script
            config = SandboxConfig(
                network=(
                    SandboxNetworkRequest(SandboxNetworkPurpose.SCRIPT)
                    if allow_network
                    else None
                ),
                resources=ResourceLimits(
                    timeout_seconds=timeout_seconds,
                    memory_mb=TRACECAT__SANDBOX_DEFAULT_MEMORY_MB,
                ),
                env_vars=self._with_action_gateway_socket_env(
                    env_vars,
                    socket_path=resolved_action_gateway_socket,
                    env_socket_path=RUN_PYTHON_ACTION_GATEWAY_SOCKET,
                )
                or {},
                dependencies=dependencies or [],
                python_path_dirs=python_path_dirs or [],
                action_gateway_socket=resolved_action_gateway_socket,
            )

            await self._prepare_execution(job_dir, script, inputs)
            result = await self.nsjail_executor.execute(job_dir, config, cache_key)

            if not result.success:
                # Full Python error is exposed to users
                error_msg = result.error or "Unknown error"
                logger.error(
                    "Script execution failed",
                    error=error_msg,
                    stdout=result.stdout[:500] if result.stdout else None,
                    stderr=result.stderr[:500] if result.stderr else None,
                )
                # Envelope errors may carry a structured dict; the typed
                # exceptions take plain strings only.
                message = (
                    error_msg
                    if isinstance(error_msg, str)
                    else "Sandbox execution failed"
                )
                raise_for_sandbox_error_code(result.error_code, message)
                raise SandboxExecutionError(message)

            return result.output
