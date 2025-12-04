"""High-level sandbox service for Python script execution."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tracecat.config import (
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_DEFAULT_MEMORY_MB,
    TRACECAT__SANDBOX_DEFAULT_TIMEOUT,
)
from tracecat.logger import logger
from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxExecutionError,
)
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import ResourceLimits, SandboxConfig
from tracecat.sandbox.wrapper import INSTALL_SCRIPT, WRAPPER_SCRIPT


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
        self.executor = NsjailExecutor()
        self.cache_dir = Path(cache_dir)
        self.package_cache = self.cache_dir / "packages"
        self.uv_cache = self.cache_dir / "uv-cache"

        # Ensure cache directories exist
        self.package_cache.mkdir(parents=True, exist_ok=True)
        self.uv_cache.mkdir(parents=True, exist_ok=True)

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
        result = await self.executor.execute_install(
            job_dir, cache_key, timeout_seconds
        )

        if not result.success:
            logger.error(
                "Package installation failed",
                dependencies=dependencies,
                error=result.error,
                stderr=result.stderr[:500],
            )
            raise PackageInstallError(
                f"Failed to install packages: {result.error or 'Unknown error'}"
            )

        # Copy installed packages to shared cache
        site_packages = cache_dir / "site-packages"
        if site_packages.exists() and any(site_packages.iterdir()):
            dest = self.package_cache / cache_key / "site-packages"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(site_packages, dest)
            logger.info(
                "Packages cached",
                cache_key=cache_key,
                path=str(dest),
            )
        else:
            logger.warning(
                "No packages were installed",
                dependencies=dependencies,
            )

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
        workspace_id: str | None = None,
    ) -> Any:
        """Execute a Python script in the nsjail sandbox.

        This is the main entry point for script execution. It handles:
        1. Package installation (if dependencies specified)
        2. Script execution with proper isolation

        Args:
            script: Python script content to execute.
            inputs: Dictionary of inputs passed to the main function.
            dependencies: List of pip packages to install.
            timeout_seconds: Maximum execution time (default from config).
            allow_network: Whether to allow network access during execution.
            env_vars: Environment variables to set in the sandbox.
            workspace_id: Optional workspace ID for multi-tenant cache isolation.
                When provided, package caches are scoped to the workspace,
                preventing cross-workspace package poisoning attacks.

        Returns:
            The return value of the script's main function.

        Raises:
            SandboxExecutionError: If script execution fails.
            PackageInstallError: If package installation fails.
            SandboxTimeoutError: If execution times out.
        """
        if timeout_seconds is None:
            timeout_seconds = TRACECAT__SANDBOX_DEFAULT_TIMEOUT

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
                network_enabled=allow_network,
                resources=ResourceLimits(
                    timeout_seconds=timeout_seconds,
                    memory_mb=TRACECAT__SANDBOX_DEFAULT_MEMORY_MB,
                ),
                env_vars=env_vars or {},
                dependencies=dependencies or [],
            )

            await self._prepare_execution(job_dir, script, inputs)
            result = await self.executor.execute(job_dir, config, cache_key)

            if not result.success:
                # Full Python error is exposed to users
                error_msg = result.error or "Unknown error"
                logger.error(
                    "Script execution failed",
                    error=error_msg,
                    stdout=result.stdout[:500] if result.stdout else None,
                    stderr=result.stderr[:500] if result.stderr else None,
                )
                raise SandboxExecutionError(error_msg)

            return result.output
