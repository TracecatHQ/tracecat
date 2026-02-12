"""nsjail executor for sandboxed Python execution."""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tracecat.config import (
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS,
    TRACECAT__SANDBOX_PYPI_INDEX_URL,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger
from tracecat.sandbox.exceptions import SandboxTimeoutError, SandboxValidationError
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, SandboxResult


@dataclass
class ActionSandboxConfig:
    """Configuration for action sandbox execution (untrusted mode only).

    All sandbox execution is untrusted - DB credentials are never passed.
    Secrets and variables must be pre-resolved before sandbox execution.

    Attributes:
        registry_paths: List of directories containing extracted registry tarballs.
            Mounted at /packages/0, /packages/1, etc. in deterministic order.
        tracecat_app_dir: Directory containing tracecat package (not mounted in untrusted mode).
        site_packages_dir: Directory containing Python site-packages (not mounted in untrusted mode).
        env_vars: Environment variables to inject (SDK context, NOT DB credentials).
        resources: Resource limits for the sandbox.
        timeout_seconds: Maximum execution time in seconds.
    """

    registry_paths: list[Path]
    tracecat_app_dir: Path
    site_packages_dir: Path | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    timeout_seconds: float = 300


# Valid environment variable name pattern (POSIX compliant)
_ENV_VAR_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Valid cache key pattern (SHA256 hex string)
_CACHE_KEY_PATTERN = re.compile(r"^[a-f0-9]+$")

# Minimal base environment for sandboxed processes
SANDBOX_BASE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

_PASTA_GATEWAY_IP = "10.255.255.1"


def build_sandbox_resolv_conf() -> str:
    """Build sandbox resolv.conf with pasta DNS plus host search/options lines.

    In Kubernetes, short service names rely on search domains like
    `*.svc.cluster.local` and resolver options from the pod's /etc/resolv.conf.
    Preserve those lines while forcing nameserver to pasta's DNS gateway.
    """
    lines = [f"nameserver {_PASTA_GATEWAY_IP}"]
    try:
        host_resolv = Path("/etc/resolv.conf").read_text()
        for line in host_resolv.splitlines():
            stripped = line.strip()
            if stripped.startswith("search ") or stripped.startswith("options "):
                lines.append(stripped)
    except OSError:
        pass
    return "\n".join(lines) + "\n"


_NSJAIL_HINT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bCLONE_NEWUSER\b|clone_newuser", re.IGNORECASE),
        "User namespaces appear to be unavailable. Ensure USERNS is enabled on the host "
        "(e.g., sysctls like kernel.unprivileged_userns_clone and user.max_user_namespaces).",
    ),
    (
        re.compile(r"\bOperation not permitted\b|\bEPERM\b", re.IGNORECASE),
        "nsjail was denied a required operation (often mount/namespace). In containers, ensure "
        "CAP_SYS_ADMIN is present and seccomp/AppArmor/SELinux policies allow mounts "
        "(Docker commonly needs SYS_ADMIN + seccomp:unconfined; some hosts also need AppArmor unconfined).",
    ),
    (
        re.compile(r"/dev/net/tun|TUN|tun", re.IGNORECASE),
        "Userspace networking (pasta) may require /dev/net/tun. Ensure the container/pod "
        "has the TUN device available and passt/pasta is installed in the image.",
    ),
]


def _nsjail_failure_hint(stderr: str) -> str | None:
    stderr = stderr.strip()
    if not stderr:
        return None
    for pattern, hint in _NSJAIL_HINT_PATTERNS:
        if pattern.search(stderr):
            return hint
    return None


def _validate_env_key(key: str) -> None:
    """Validate environment variable key is safe for protobuf config.

    Args:
        key: Environment variable name to validate.

    Raises:
        SandboxValidationError: If key contains invalid characters.
    """
    if not _ENV_VAR_KEY_PATTERN.match(key):
        raise SandboxValidationError(
            f"Invalid environment variable key: {key!r}. "
            "Keys must match pattern [A-Za-z_][A-Za-z0-9_]*"
        )


def _validate_path(path: Path, name: str) -> None:
    """Validate path is safe for protobuf config interpolation.

    Args:
        path: Path to validate.
        name: Human-readable name for error messages.

    Raises:
        SandboxValidationError: If path contains dangerous characters.
    """
    path_str = str(path)
    # Characters that could break protobuf text format parsing
    dangerous_chars = {'"', "'", "\n", "\r", "\\", "{", "}"}
    found_chars = [c for c in dangerous_chars if c in path_str]
    if found_chars:
        raise SandboxValidationError(
            f"Invalid {name} path: contains dangerous characters {found_chars!r}"
        )


def _validate_cache_key(cache_key: str) -> None:
    """Validate cache key is a safe hex string.

    Args:
        cache_key: Cache key to validate (expected to be SHA256 hex).

    Raises:
        SandboxValidationError: If cache key is not a valid hex string.
    """
    if not _CACHE_KEY_PATTERN.match(cache_key):
        raise SandboxValidationError(
            f"Invalid cache_key: {cache_key!r}. Must be a lowercase hex string."
        )


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

        Raises:
            SandboxValidationError: If any input contains dangerous characters.
        """
        # Validate inputs to prevent injection into protobuf config
        _validate_path(job_dir, "job_dir")
        _validate_path(self.rootfs, "rootfs")
        if cache_key:
            _validate_cache_key(cache_key)

        # Determine if network should be enabled
        # - Install phase: always enabled for package downloads
        # - Execute phase: per config.network_enabled
        network_enabled = phase == "install" or config.network_enabled

        # Network namespace is always isolated (clone_newnet: true)
        # When network access is needed, pasta provides userspace networking
        lines = [
            'name: "python_sandbox"',
            "mode: ONCE",
            'hostname: "sandbox"',
            "keep_env: false",
            "",
            "# Namespace isolation - network always isolated, pasta for outbound access",
            "clone_newnet: true",
            "clone_newuser: true",
            "clone_newns: true",
            "clone_newpid: true",
            "clone_newipc: true",
            "clone_newuts: true",
        ]

        # Userspace networking via pasta when network access is needed
        if network_enabled:
            lines.extend(
                [
                    "",
                    "# Userspace networking via pasta - provides internet access with isolation",
                    "user_net {",
                    "  enable: true",
                    '  ip: "10.255.255.2"',
                    '  gw: "10.255.255.1"',
                    '  ip6: "fc00::2"',
                    '  gw6: "fc00::1"',
                    "  enable_dns: true",
                    "}",
                ]
            )

        lines.extend(
            [
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
        )

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

        # Network config: when using pasta, generate /etc files for hostname resolution
        # Docker export leaves these empty since Docker manages them at runtime
        if network_enabled:
            resolv_conf_path = job_dir / "resolv.conf"
            resolv_conf_path.write_text(build_sandbox_resolv_conf())

            hosts_path = job_dir / "hosts"
            hosts_path.write_text(
                "127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n"
            )

            # nsswitch.conf tells glibc how to resolve hostnames: check /etc/hosts
            # first ("files"), then fall back to DNS. Without this, hostname
            # resolution may fail even with valid /etc/hosts and /etc/resolv.conf.
            nsswitch_path = job_dir / "nsswitch.conf"
            nsswitch_path.write_text(
                "passwd:         files\n"
                "group:          files\n"
                "shadow:         files\n"
                "hosts:          files dns\n"
                "networks:       files\n"
                "protocols:      files\n"
                "services:       files\n"
            )

            lines.extend(
                [
                    "",
                    "# Network config - DNS and hostname resolution",
                    f'mount {{ src: "{resolv_conf_path}" dst: "/etc/resolv.conf" is_bind: true rw: false }}',
                    f'mount {{ src: "{hosts_path}" dst: "/etc/hosts" is_bind: true rw: false }}',
                    f'mount {{ src: "{nsswitch_path}" dst: "/etc/nsswitch.conf" is_bind: true rw: false }}',
                ]
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

    def _build_env_map(
        self,
        config: SandboxConfig,
        phase: Literal["install", "execute"],
        cache_key: str | None = None,
    ) -> dict[str, str]:
        """Construct a sanitized environment for the nsjail process."""
        env_map: dict[str, str] = {**SANDBOX_BASE_ENV}

        if phase == "install":
            env_map["UV_CACHE_DIR"] = "/uv-cache"
            # Pass PyPI index URLs to uv for package installation
            env_map["UV_INDEX_URL"] = TRACECAT__SANDBOX_PYPI_INDEX_URL
            if TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS:
                env_map["UV_EXTRA_INDEX_URL"] = ",".join(
                    TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS
                )
        elif cache_key:
            cache_path = self.package_cache / cache_key / "site-packages"
            if cache_path.exists():
                env_map["PYTHONPATH"] = "/packages"

        for key, value in config.env_vars.items():
            _validate_env_key(key)
            env_map[key] = value

        return env_map

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
        config_path.chmod(0o600)

        env_map = self._build_env_map(config, "execute", cache_key)
        env_args: list[str] = []
        for key in env_map:
            env_args.extend(["--env", key])

        # Build nsjail command - script is in config, no args after --
        cmd = [
            str(self.nsjail_path),
            "--config",
            str(config_path),
            *env_args,
        ]

        logger.debug(
            "Executing nsjail command",
            cmd=cmd,
            job_dir=str(job_dir),
            cache_key=cache_key,
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env_map,
        )

        try:
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

        finally:
            # Defense-in-depth: Clean up config file to avoid leaving artifacts
            # Job dir cleanup will also handle this, but early removal is safer
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass  # Best effort cleanup

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
            hint = _nsjail_failure_hint(stderr)
            error_msg = "Sandbox execution failed"
            if hint:
                error_msg = f"{error_msg}. {hint}"
            logger.error(
                "Sandbox execution failed",
                returncode=process.returncode,
                stderr=stderr[:500],
            )
            return SandboxResult(
                success=False,
                error=error_msg,
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
        config_path.chmod(0o600)

        env_map = self._build_env_map(config, "install", cache_key)
        env_args: list[str] = []
        for key in env_map:
            env_args.extend(["--env", key])

        # Build nsjail command - script is in config
        cmd = [
            str(self.nsjail_path),
            "--config",
            str(config_path),
            *env_args,
        ]

        start_time = time.time()

        logger.debug(
            "Executing package installation",
            cmd=cmd,
            job_dir=str(job_dir),
            cache_key=cache_key,
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env_map,
        )

        try:
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

        finally:
            # Defense-in-depth: Clean up config file to avoid leaving artifacts
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass  # Best effort cleanup

        execution_time_ms = (time.time() - start_time) * 1000
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        success = process.returncode == 0

        if not success:
            hint = _nsjail_failure_hint(stderr)
            if hint:
                stderr = f"{stderr.rstrip()}\n\nnsjail hint: {hint}\n"
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

    def _build_action_config(
        self,
        job_dir: Path,
        config: ActionSandboxConfig,
    ) -> str:
        """Build nsjail protobuf config for action execution.

        Args:
            job_dir: Directory containing input.json (mounted at /work).
            config: Action sandbox configuration.

        Returns:
            nsjail protobuf configuration as a string.

        Raises:
            SandboxValidationError: If any input contains dangerous characters.
        """
        # Validate inputs to prevent injection into protobuf config
        _validate_path(job_dir, "job_dir")
        _validate_path(self.rootfs, "rootfs")
        for i, registry_path in enumerate(config.registry_paths):
            _validate_path(registry_path, f"registry_path_{i}")
        _validate_path(config.tracecat_app_dir, "tracecat_app_dir")
        if config.site_packages_dir:
            _validate_path(config.site_packages_dir, "site_packages_dir")

        # Network namespace isolated with pasta for userspace networking
        # This provides outbound connectivity while maintaining network isolation
        lines = [
            'name: "action_sandbox"',
            "mode: ONCE",
            'hostname: "sandbox"',
            "keep_env: false",
            "",
            "# Namespace isolation - network isolated with pasta for outbound access",
            "clone_newnet: true",
            "clone_newuser: true",
            "clone_newns: true",
            "clone_newpid: true",
            "clone_newipc: true",
            "clone_newuts: true",
            "",
            "# Userspace networking via pasta - provides internet access with isolation",
            "user_net {",
            "  enable: true",
            '  ip: "10.255.255.2"',
            '  gw: "10.255.255.1"',
            '  ip6: "fc00::2"',
            '  gw6: "fc00::1"',
            "  enable_dns: true",
            "}",
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

        # Network config: pasta provides DNS forwarding at the gateway IP (10.255.255.1)
        # Docker export leaves /etc files empty since Docker manages them at runtime
        resolv_conf_path = job_dir / "resolv.conf"
        resolv_conf_path.write_text(build_sandbox_resolv_conf())

        hosts_path = job_dir / "hosts"
        hosts_path.write_text(
            "127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n"
        )

        # nsswitch.conf tells glibc how to resolve hostnames: check /etc/hosts
        # first ("files"), then fall back to DNS. Without this, hostname
        # resolution may fail even with valid /etc/hosts and /etc/resolv.conf.
        nsswitch_path = job_dir / "nsswitch.conf"
        nsswitch_path.write_text(
            "passwd:         files\n"
            "group:          files\n"
            "shadow:         files\n"
            "hosts:          files dns\n"
            "networks:       files\n"
            "protocols:      files\n"
            "services:       files\n"
        )

        lines.extend(
            [
                "",
                "# Network config - DNS and hostname resolution",
                f'mount {{ src: "{resolv_conf_path}" dst: "/etc/resolv.conf" is_bind: true rw: false }}',
                f'mount {{ src: "{hosts_path}" dst: "/etc/hosts" is_bind: true rw: false }}',
                f'mount {{ src: "{nsswitch_path}" dst: "/etc/nsswitch.conf" is_bind: true rw: false }}',
                "",
                "# /dev essentials",
                'mount { src: "/dev/null" dst: "/dev/null" is_bind: true rw: true }',
                'mount { src: "/dev/urandom" dst: "/dev/urandom" is_bind: true rw: false }',
                'mount { src: "/dev/random" dst: "/dev/random" is_bind: true rw: false }',
                'mount { src: "/dev/zero" dst: "/dev/zero" is_bind: true rw: false }',
                "",
                "# Temporary filesystems",
                'mount { dst: "/tmp" fstype: "tmpfs" rw: true options: "size=256M" }',
                'mount { dst: "/proc" fstype: "proc" rw: false }',
                "",
                "# Action execution mounts",
                f'mount {{ src: "{job_dir}" dst: "/work" is_bind: true rw: true }}',
            ]
        )

        # Mount each registry path at /packages/0, /packages/1, etc.
        for i, registry_path in enumerate(config.registry_paths):
            if registry_path.exists():
                lines.append(
                    f'mount {{ src: "{registry_path}" dst: "/packages/{i}" is_bind: true rw: false }}'
                )

        # NOTE: /app and /site-packages are NOT mounted in untrusted mode
        # Untrusted mode uses minimal_runner.py copied to /work, no tracecat imports

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
                f"time_limit: {int(config.timeout_seconds)}",
            ]
        )

        # Execution settings - always use minimal_runner.py (untrusted mode)
        # minimal_runner.py is copied to /work and doesn't need tracecat imports
        lines.extend(
            [
                "",
                "# Execution",
                'cwd: "/work"',
                'exec_bin { path: "/usr/local/bin/python3" arg: "/work/minimal_runner.py" }',
            ]
        )

        return "\n".join(lines)

    def _build_action_env_map(
        self,
        config: ActionSandboxConfig,
    ) -> dict[str, str]:
        """Construct environment for action sandbox execution (untrusted mode)."""
        env_map: dict[str, str] = {**SANDBOX_BASE_ENV}

        # Set PYTHONPATH with multiple registry paths: /packages/0:/packages/1:...
        # NOTE: /app and /site-packages are NOT mounted in untrusted mode
        pythonpath_parts = []
        for i, registry_path in enumerate(config.registry_paths):
            if registry_path.exists():
                pythonpath_parts.append(f"/packages/{i}")

        if pythonpath_parts:
            env_map["PYTHONPATH"] = ":".join(pythonpath_parts)

        # Add user-provided env vars (SDK context, NOT DB credentials)
        for key, value in config.env_vars.items():
            _validate_env_key(key)
            env_map[key] = value

        return env_map

    async def execute_action(
        self,
        job_dir: Path,
        config: ActionSandboxConfig,
    ) -> SandboxResult:
        """Execute a registry action inside the nsjail sandbox.

        Args:
            job_dir: Directory containing input.json (will be mounted at /work).
            config: Action sandbox configuration.

        Returns:
            SandboxResult with execution outcome.
            The result.json file in job_dir contains the action result.
        """
        start_time = time.time()

        # Generate nsjail config for action execution
        nsjail_config = self._build_action_config(job_dir, config)

        # Write config to job directory
        config_path = job_dir / "nsjail.cfg"
        config_path.write_text(nsjail_config)
        config_path.chmod(0o600)

        env_map = self._build_action_env_map(config)
        env_args: list[str] = []
        for key in env_map:
            env_args.extend(["--env", key])

        # Build nsjail command
        cmd = [
            str(self.nsjail_path),
            "--config",
            str(config_path),
            *env_args,
        ]

        logger.debug(
            "Executing action in nsjail sandbox",
            cmd=cmd,
            job_dir=str(job_dir),
            registry_paths=config.registry_paths,
            tracecat_app=str(config.tracecat_app_dir),
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env_map,
        )

        try:
            # Wait with timeout (add buffer for nsjail overhead)
            timeout = config.timeout_seconds + 10
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

        except TimeoutError as e:
            process.kill()
            await process.wait()
            raise SandboxTimeoutError(
                f"Action execution timed out after {config.timeout_seconds}s"
            ) from e

        finally:
            # Clean up config file
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass

        execution_time_ms = (time.time() - start_time) * 1000
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Try to parse result.json for structured output
        result_path = job_dir / "result.json"
        if result_path.exists():
            try:
                result_data = json.loads(result_path.read_text())
                # Log subprocess stderr for debugging (contains timing info)
                # Filter out nsjail verbose output, look for Python logs
                if stderr.strip():
                    # Extract lines that look like Python logs (not nsjail [I] lines)
                    python_logs = "\n".join(
                        line
                        for line in stderr.split("\n")
                        if not line.startswith("[I]") and not line.startswith("[W]")
                    )
                    if python_logs.strip():
                        logger.info("Subprocess output", output=python_logs[:2000])
                return SandboxResult(
                    success=result_data.get("success", False),
                    output=result_data.get("result"),
                    stdout=stdout,
                    stderr=stderr,
                    error=result_data.get("error"),
                    exit_code=process.returncode,
                    execution_time_ms=execution_time_ms,
                )
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse action result.json", path=str(result_path)
                )

        # No result.json - infrastructure error
        if process.returncode != 0:
            hint = _nsjail_failure_hint(stderr)
            error_msg = "Action sandbox execution failed"
            if hint:
                error_msg = f"{error_msg}. {hint}"
            logger.error(
                "Action sandbox execution failed",
                returncode=process.returncode,
                stderr=stderr[-2000:],
            )
            return SandboxResult(
                success=False,
                error=error_msg,
                stdout=stdout,
                stderr=stderr[:2000],
                exit_code=process.returncode,
                execution_time_ms=execution_time_ms,
            )

        # Process succeeded but no result.json
        return SandboxResult(
            success=True,
            output=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            execution_time_ms=execution_time_ms,
        )
