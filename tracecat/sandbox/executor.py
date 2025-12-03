"""nsjail executor for sandboxed Python execution."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tracecat.config import (
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.types import SandboxConfig, SandboxResult

if TYPE_CHECKING:
    pass


class NsjailExecutor:
    """Low-level nsjail invocation for sandboxed Python execution.

    Handles the execution of Python scripts inside an nsjail sandbox with:
    - Namespace isolation (PID, network, mount, IPC, UTS)
    - Resource limits (memory, CPU, file size, processes)
    - Read-only rootfs with specific writable mounts
    - Configurable network access
    """

    def __init__(
        self,
        nsjail_path: str = TRACECAT__SANDBOX_NSJAIL_PATH,
        rootfs_path: str = TRACECAT__SANDBOX_ROOTFS_PATH,
        cache_dir: str = TRACECAT__SANDBOX_CACHE_DIR,
    ):
        self.nsjail_path = Path(nsjail_path)
        self.rootfs = Path(rootfs_path)
        self.cache_dir = Path(cache_dir)
        self.package_cache = self.cache_dir / "packages"
        self.uv_cache = self.cache_dir / "uv-cache"

    def _build_config(
        self,
        job_dir: Path,
        phase: Literal["install", "execute"],
        config: SandboxConfig,
        cache_key: str | None = None,
        script_name: str = "wrapper.py",
    ) -> str:
        """Build nsjail protobuf config.

        Args:
            job_dir: Directory containing the job files.
            phase: Either "install" (package installation) or "execute" (script execution).
            config: Sandbox configuration.
            cache_key: Cache key for package lookup.
            script_name: Name of the script to execute (for execute phase).

        Returns:
            nsjail protobuf configuration as a string.
        """
        # Determine if network should be enabled
        # - Install phase: always enabled for package downloads
        # - Execute phase: per config.network_enabled
        network_enabled = phase == "install" or config.network_enabled

        lines = [
            'name: "python_sandbox"',
            "mode: ONCE",
            'hostname: "sandbox"',
            "",
            "# Namespace isolation",
            f"clone_newnet: {'false' if network_enabled else 'true'}",
            "clone_newuser: true",
            "clone_newns: true",
            "clone_newpid: true",
            "clone_newipc: true",
            "clone_newuts: true",
            "",
            "# UID/GID mapping - map container user to current user",
            f'uidmap {{ inside_id: "1000" outside_id: "{os.getuid()}" count: 1 }}',
            f'gidmap {{ inside_id: "1000" outside_id: "{os.getgid()}" count: 1 }}',
            "",
            "# Rootfs mounts - read-only base system",
            f'mount {{ src: "{self.rootfs}/usr" dst: "/usr" is_bind: true rw: false }}',
            f'mount {{ src: "{self.rootfs}/lib" dst: "/lib" is_bind: true rw: false }}',
            f'mount {{ src: "{self.rootfs}/bin" dst: "/bin" is_bind: true rw: false }}',
            f'mount {{ src: "{self.rootfs}/etc" dst: "/etc" is_bind: true rw: false }}',
        ]

        # Optional mounts - only include if the directories exist in rootfs
        lib64_path = self.rootfs / "lib64"
        if lib64_path.exists():
            lines.append(
                f'mount {{ src: "{lib64_path}" dst: "/lib64" is_bind: true rw: false }}'
            )

        sbin_path = self.rootfs / "sbin"
        if sbin_path.exists():
            lines.append(
                f'mount {{ src: "{sbin_path}" dst: "/sbin" is_bind: true rw: false }}'
            )

        lines.extend(
            [
                "",
                "# /dev essentials",
                'mount { src: "/dev/null" dst: "/dev/null" is_bind: true rw: true }',
                'mount { src: "/dev/urandom" dst: "/dev/urandom" is_bind: true rw: false }',
                'mount { src: "/dev/random" dst: "/dev/random" is_bind: true rw: false }',
                'mount { src: "/dev/zero" dst: "/dev/zero" is_bind: true rw: false }',
                "",
                "# Temporary filesystems",
                'mount { dst: "/tmp" fstype: "tmpfs" rw: true options: "size=256M" }',
                # Bind mount /proc from host instead of creating new procfs
                # (new procfs mount fails in Docker due to masked paths in /proc)
                'mount { src: "/proc" dst: "/proc" is_bind: true rw: false }',
            ]
        )

        # Phase-specific mounts
        if phase == "install":
            # Writable cache for package installation
            lines.extend(
                [
                    "",
                    "# Install phase mounts - writable cache",
                    f'mount {{ src: "{job_dir}/cache" dst: "/cache" is_bind: true rw: true }}',
                    f'mount {{ src: "{self.uv_cache}" dst: "/uv-cache" is_bind: true rw: true }}',
                    f'mount {{ src: "{job_dir}" dst: "/work" is_bind: true rw: true }}',
                ]
            )
        else:
            # Execute phase mounts
            lines.extend(
                [
                    "",
                    "# Execute phase mounts - read-only packages, writable work dir",
                ]
            )
            if cache_key:
                cache_path = self.package_cache / cache_key / "site-packages"
                if cache_path.exists():
                    lines.append(
                        f'mount {{ src: "{cache_path}" dst: "/packages" is_bind: true rw: false }}'
                    )
            lines.append(
                f'mount {{ src: "{job_dir}" dst: "/work" is_bind: true rw: true }}'
            )

        # Resource limits
        lines.extend(
            [
                "",
                "# Resource limits",
                f"rlimit_as: {config.resources.memory_mb * 1024 * 1024}",
                f"rlimit_cpu: {config.resources.cpu_seconds}",
                f"rlimit_fsize: {config.resources.max_file_size_mb * 1024 * 1024}",
                f"rlimit_nofile: {config.resources.max_open_files}",
                f"rlimit_nproc: {config.resources.max_processes}",
                f"time_limit: {config.resources.timeout_seconds}",
            ]
        )

        # Environment variables
        lines.extend(
            [
                "",
                "# Environment variables",
                'envar: "PATH=/usr/local/bin:/usr/bin:/bin"',
                'envar: "HOME=/tmp"',
                'envar: "PYTHONDONTWRITEBYTECODE=1"',
                'envar: "PYTHONUNBUFFERED=1"',
            ]
        )

        if phase == "install":
            lines.append('envar: "UV_CACHE_DIR=/uv-cache"')
        else:
            if cache_key:
                cache_path = self.package_cache / cache_key / "site-packages"
                if cache_path.exists():
                    lines.append('envar: "PYTHONPATH=/packages"')
            # Add user-provided environment variables
            for key, value in config.env_vars.items():
                # Escape double quotes in values
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'envar: "{key}={escaped}"')

        # Execution settings - script path must be in exec_bin for config file mode
        script_path = f"/work/{script_name}"
        lines.extend(
            [
                "",
                "# Execution",
                'cwd: "/work"',
                f'exec_bin {{ path: "/usr/local/bin/python3" arg: "{script_path}" }}',
            ]
        )

        return "\n".join(lines)

    async def execute(
        self,
        job_dir: Path,
        config: SandboxConfig,
        cache_key: str | None = None,
        script_name: str = "wrapper.py",
    ) -> SandboxResult:
        """Execute a Python script inside the nsjail sandbox.

        Args:
            job_dir: Directory containing the job files (script.py, inputs.json, wrapper.py).
            config: Sandbox configuration.
            cache_key: Cache key for package lookup.
            script_name: Name of the script to execute (default: wrapper.py).

        Returns:
            SandboxResult with execution outcome.
        """
        start_time = time.time()

        # Generate nsjail config with script name embedded
        nsjail_config = self._build_config(
            job_dir, "execute", config, cache_key, script_name
        )

        # Write config to job directory
        config_path = job_dir / "nsjail.cfg"
        config_path.write_text(nsjail_config)

        # Build nsjail command - script is in config, no args after --
        cmd = [
            str(self.nsjail_path),
            "--config",
            str(config_path),
        ]

        logger.debug(
            "Executing nsjail command",
            cmd=cmd,
            job_dir=str(job_dir),
            cache_key=cache_key,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(job_dir),
            )

            # Wait with timeout (add buffer for nsjail overhead)
            timeout = config.resources.timeout_seconds + 10
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

        except TimeoutError as e:
            # Kill the process if it times out
            process.kill()
            await process.wait()
            raise SandboxTimeoutError(
                f"Execution timed out after {config.resources.timeout_seconds}s"
            ) from e

        execution_time_ms = (time.time() - start_time) * 1000
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Try to parse result.json for structured output
        result_path = job_dir / "result.json"
        if result_path.exists():
            try:
                result_data = json.loads(result_path.read_text())
                return SandboxResult(
                    success=result_data.get("success", False),
                    output=result_data.get("output"),
                    stdout=result_data.get("stdout", stdout),
                    stderr=result_data.get("stderr", stderr),
                    error=result_data.get("error"),
                    exit_code=process.returncode,
                    execution_time_ms=execution_time_ms,
                )
            except json.JSONDecodeError:
                logger.warning("Failed to parse result.json", path=str(result_path))

        # No result.json - this is an infrastructure error
        if process.returncode != 0:
            # Don't expose nsjail internals to users
            logger.error(
                "Sandbox execution failed",
                returncode=process.returncode,
                stderr=stderr[:500],
            )
            return SandboxResult(
                success=False,
                error="Sandbox execution failed",
                stdout=stdout,
                stderr=stderr[:500],  # Truncate for debugging
                exit_code=process.returncode,
                execution_time_ms=execution_time_ms,
            )

        # Process succeeded but no result.json (shouldn't happen with wrapper)
        return SandboxResult(
            success=True,
            output=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            execution_time_ms=execution_time_ms,
        )

    async def execute_install(
        self,
        job_dir: Path,
        cache_key: str,
        timeout_seconds: int = 300,
    ) -> SandboxResult:
        """Execute package installation inside the nsjail sandbox.

        Args:
            job_dir: Directory containing the install script.
            cache_key: Cache key for the packages being installed.
            timeout_seconds: Maximum time for installation.

        Returns:
            SandboxResult with installation outcome.
        """
        from tracecat.sandbox.types import ResourceLimits

        # Create config for installation (always with network)
        config = SandboxConfig(
            network_enabled=True,
            resources=ResourceLimits(
                timeout_seconds=timeout_seconds,
                memory_mb=2048,  # Same as execution
            ),
        )

        # Generate nsjail config for install phase with install.py script
        nsjail_config = self._build_config(
            job_dir, "install", config, cache_key, script_name="install.py"
        )

        # Write config to job directory
        config_path = job_dir / "nsjail.cfg"
        config_path.write_text(nsjail_config)

        # Build nsjail command - script is in config
        cmd = [
            str(self.nsjail_path),
            "--config",
            str(config_path),
        ]

        start_time = time.time()

        logger.debug(
            "Executing package installation",
            cmd=cmd,
            job_dir=str(job_dir),
            cache_key=cache_key,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(job_dir),
            )

            timeout = timeout_seconds + 30  # Extra buffer for package downloads
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

        except TimeoutError as e:
            process.kill()
            await process.wait()
            raise SandboxTimeoutError(
                f"Package installation timed out after {timeout_seconds}s"
            ) from e

        execution_time_ms = (time.time() - start_time) * 1000
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        success = process.returncode == 0

        if not success:
            logger.error(
                "Package installation failed",
                returncode=process.returncode,
                stderr=stderr[:1000],
            )

        return SandboxResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            error=stderr if not success else None,
            exit_code=process.returncode,
            execution_time_ms=execution_time_ms,
        )
