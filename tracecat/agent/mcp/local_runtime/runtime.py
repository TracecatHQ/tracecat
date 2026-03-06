"""Runtime for ephemeral local stdio MCP catalog discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from tracecat import config
from tracecat.agent.mcp.catalog import (
    MCPServerCatalog,
    list_optional_capability,
    normalize_prompt,
    normalize_resource,
    normalize_tool,
)
from tracecat.agent.mcp.local_runtime.types import (
    LocalMCPDiscoveryConfig,
    LocalMCPDiscoveryError,
    LocalMCPDiscoveryPhase,
)
from tracecat.integrations.mcp_validation import validate_mcp_command_config
from tracecat.logger import logger
from tracecat.sandbox.utils import is_nsjail_available

_PACKAGE_FETCH_HINTS = (
    "npm error",
    "npm err!",
    "could not resolve",
    "failed to download",
    "no matching distribution found",
    "unable to fetch",
)
_LOCAL_SANDBOX_SEMAPHORE: asyncio.Semaphore | None = None
_JAILED_HOME_DIR = Path("/home/agent")
_JAILED_UV_CACHE_DIR = Path("/cache/uv")
_JAILED_NPM_CACHE_DIR = Path("/cache/npm")
_JAILED_EGRESS_GUARD_LIB = "/usr/local/lib/libtracecat_mcp_egress_guard.so"
_NSJAIL_VISIBLE_PATH_PREFIXES = ("/usr/", "/bin/")


def _get_local_sandbox_semaphore() -> asyncio.Semaphore:
    global _LOCAL_SANDBOX_SEMAPHORE
    if _LOCAL_SANDBOX_SEMAPHORE is None:
        _LOCAL_SANDBOX_SEMAPHORE = asyncio.Semaphore(
            config.TRACECAT__MCP_MAX_CONCURRENT_LOCAL_SANDBOXES
        )
    return _LOCAL_SANDBOX_SEMAPHORE


def _phase_summary(phase: LocalMCPDiscoveryPhase) -> str:
    match phase:
        case LocalMCPDiscoveryPhase.CONFIG_VALIDATION:
            return "The local MCP command configuration is invalid."
        case LocalMCPDiscoveryPhase.PACKAGE_FETCH_INSTALL:
            return "The local MCP package could not be fetched or installed."
        case LocalMCPDiscoveryPhase.PROCESS_SPAWN:
            return "The local MCP process could not be started."
        case LocalMCPDiscoveryPhase.INITIALIZE_HANDSHAKE:
            return "The local MCP server failed during initialization."
        case LocalMCPDiscoveryPhase.LIST_TOOLS:
            return "The local MCP server failed while listing tools."
        case LocalMCPDiscoveryPhase.LIST_RESOURCES:
            return "The local MCP server failed while listing resources."
        case LocalMCPDiscoveryPhase.LIST_PROMPTS:
            return "The local MCP server failed while listing prompts."
        case LocalMCPDiscoveryPhase.TIMEOUT:
            return "Local MCP discovery timed out."
        case LocalMCPDiscoveryPhase.SHUTDOWN:
            return "The local MCP server failed during shutdown."


def _config_validation_error(
    summary: str,
    **details: object,
) -> LocalMCPDiscoveryError:
    return LocalMCPDiscoveryError(
        phase=LocalMCPDiscoveryPhase.CONFIG_VALIDATION,
        summary=summary,
        details={key: value for key, value in details.items() if value is not None},
    )


@dataclass(frozen=True, slots=True)
class _ResolvedEgressPolicy:
    allow_cidrs: tuple[str, ...] = ()
    deny_cidrs: tuple[str, ...] = ()
    hosts_lines: tuple[str, ...] = ()
    use_hosts_only_resolution: bool = False

    @property
    def requires_guard(self) -> bool:
        return bool(self.allow_cidrs or self.deny_cidrs)


def _stderr_tail(stderr_path: Path) -> str | None:
    if not stderr_path.exists():
        return None
    data = stderr_path.read_text(errors="replace").strip()
    if not data:
        return None
    return data[-1000:]


def _classify_connect_failure(
    *,
    command: str,
    stderr_path: Path,
) -> LocalMCPDiscoveryPhase:
    stderr_tail = _stderr_tail(stderr_path)
    if command in {"npx", "uvx", "uv"} and stderr_tail is not None:
        lowered = stderr_tail.lower()
        if any(hint in lowered for hint in _PACKAGE_FETCH_HINTS):
            return LocalMCPDiscoveryPhase.PACKAGE_FETCH_INSTALL
    return LocalMCPDiscoveryPhase.INITIALIZE_HANDSHAKE


def _build_runtime_env(
    *,
    config_data: LocalMCPDiscoveryConfig,
    home_dir: Path,
    uv_cache_dir: Path,
    npm_cache_dir: Path,
    jailed: bool,
) -> dict[str, str]:
    env = dict(config_data.server.get("env") or {})
    env["HOME"] = str(_JAILED_HOME_DIR if jailed else home_dir)
    env["PATH"] = config.TRACECAT__SYSTEM_PATH
    env["UV_CACHE_DIR"] = str(_JAILED_UV_CACHE_DIR if jailed else uv_cache_dir)
    env["npm_config_cache"] = str(_JAILED_NPM_CACHE_DIR if jailed else npm_cache_dir)
    env["NPM_CONFIG_CACHE"] = str(_JAILED_NPM_CACHE_DIR if jailed else npm_cache_dir)
    env["TRACECAT_MCP_ALLOW_NETWORK"] = "1" if config_data.allow_network else "0"
    if config_data.egress_allowlist:
        env["TRACECAT_MCP_EGRESS_ALLOWLIST"] = ",".join(config_data.egress_allowlist)
    if config_data.egress_denylist:
        env["TRACECAT_MCP_EGRESS_DENYLIST"] = ",".join(config_data.egress_denylist)
    return env


def _prepare_runtime_dirs(
    *,
    config_data: LocalMCPDiscoveryConfig,
    job_dir: Path,
) -> tuple[Path, Path, Path]:
    cache_root = config_data.sandbox_cache_dir / config_data.organization_id
    uv_cache_dir = cache_root / "uv-cache"
    npm_cache_dir = cache_root / "npm-cache"
    home_dir = job_dir / "home"
    for path in (uv_cache_dir, npm_cache_dir, home_dir):
        path.mkdir(parents=True, exist_ok=True)
    return uv_cache_dir, npm_cache_dir, home_dir


def _normalize_cidr_or_ip(entry: str) -> str | None:
    try:
        return str(ipaddress.ip_network(entry, strict=False))
    except ValueError:
        return None


def _quote_nsjail_string(value: str) -> str:
    if "\x00" in value:
        raise _config_validation_error(
            "Local MCP discovery received a null byte in sandbox configuration."
        )
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


async def _resolve_hostname_targets(
    hostname: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            None,
            0,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise _config_validation_error(
            "Local MCP discovery could not resolve an egress policy hostname.",
            hostname=hostname,
            message=str(exc),
        ) from exc

    cidrs: list[str] = []
    hosts_lines: list[str] = []
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        match family:
            case socket.AF_INET | socket.AF_INET6:
                ip_address = str(sockaddr[0])
                if cidr := _normalize_cidr_or_ip(ip_address):
                    cidrs.append(cidr)
                    hosts_lines.append(f"{ip_address} {hostname}")
            case _:
                continue
    if not cidrs:
        raise _config_validation_error(
            "Local MCP discovery could not resolve any IPs for an egress policy hostname.",
            hostname=hostname,
        )
    return tuple(dict.fromkeys(cidrs)), tuple(dict.fromkeys(hosts_lines))


async def _resolve_egress_policy(
    config_data: LocalMCPDiscoveryConfig,
) -> _ResolvedEgressPolicy:
    allow_cidrs: list[str] = []
    deny_cidrs: list[str] = []
    hosts_lines: list[str] = []
    use_hosts_only_resolution = False

    for raw_entry in config_data.egress_allowlist:
        entry = raw_entry.strip()
        if not entry:
            raise _config_validation_error(
                "Local MCP discovery received an empty egress allowlist entry."
            )
        if cidr := _normalize_cidr_or_ip(entry):
            allow_cidrs.append(cidr)
            continue
        resolved_cidrs, resolved_hosts = await _resolve_hostname_targets(entry)
        allow_cidrs.extend(resolved_cidrs)
        hosts_lines.extend(resolved_hosts)
        use_hosts_only_resolution = True

    for raw_entry in config_data.egress_denylist:
        entry = raw_entry.strip()
        if not entry:
            raise _config_validation_error(
                "Local MCP discovery received an empty egress denylist entry."
            )
        if cidr := _normalize_cidr_or_ip(entry):
            deny_cidrs.append(cidr)
            continue
        resolved_cidrs, _resolved_hosts = await _resolve_hostname_targets(entry)
        deny_cidrs.extend(resolved_cidrs)

    return _ResolvedEgressPolicy(
        allow_cidrs=tuple(dict.fromkeys(allow_cidrs)),
        deny_cidrs=tuple(dict.fromkeys(deny_cidrs)),
        hosts_lines=tuple(dict.fromkeys(hosts_lines)),
        use_hosts_only_resolution=use_hosts_only_resolution,
    )


def _write_hosts_resolution_files(
    *,
    policy: _ResolvedEgressPolicy,
    job_dir: Path,
) -> tuple[Path, Path, Path] | None:
    if not policy.use_hosts_only_resolution:
        return None

    hosts_path = job_dir / "hosts"
    hosts_lines = ["127.0.0.1 localhost", "::1 localhost", *policy.hosts_lines]
    hosts_path.write_text("\n".join(hosts_lines) + "\n", encoding="utf-8")
    hosts_path.chmod(0o644)

    nsswitch_path = job_dir / "nsswitch.conf"
    nsswitch_path.write_text(
        "\n".join(
            [
                "passwd: files",
                "group: files",
                "shadow: files",
                "hosts: files",
                "networks: files",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    nsswitch_path.chmod(0o644)

    resolv_path = job_dir / "resolv.conf"
    resolv_path.write_text(
        "# hosts-only egress allowlist resolution\n",
        encoding="utf-8",
    )
    resolv_path.chmod(0o644)
    return hosts_path, nsswitch_path, resolv_path


def _resolve_command_path(command: str) -> str | None:
    if not (resolved_path := shutil.which(command, path=config.TRACECAT__SYSTEM_PATH)):
        return None
    if resolved_path.startswith(_NSJAIL_VISIBLE_PATH_PREFIXES):
        return resolved_path
    return None


def _build_exec_bin_line(
    *,
    command_path: str,
    args: list[str],
    policy: _ResolvedEgressPolicy,
) -> str:
    if not policy.requires_guard:
        exec_args = " ".join(f"arg: {_quote_nsjail_string(arg)}" for arg in args)
        return f"exec_bin {{ path: {_quote_nsjail_string(command_path)} {exec_args} }}"

    guard_env_args = [
        f"arg: {_quote_nsjail_string(f'LD_PRELOAD={_JAILED_EGRESS_GUARD_LIB}')}"
    ]
    if policy.allow_cidrs:
        guard_env_args.append(
            f"arg: {_quote_nsjail_string('TRACECAT_MCP_EGRESS_ALLOW_CIDRS=' + ','.join(policy.allow_cidrs))}"
        )
    if policy.deny_cidrs:
        guard_env_args.append(
            f"arg: {_quote_nsjail_string('TRACECAT_MCP_EGRESS_DENY_CIDRS=' + ','.join(policy.deny_cidrs))}"
        )
    command_args = " ".join(f"arg: {_quote_nsjail_string(arg)}" for arg in args)
    segments = [
        f"exec_bin {{ path: {_quote_nsjail_string('/usr/bin/env')}",
        *guard_env_args,
        f"arg: {_quote_nsjail_string(command_path)}",
        command_args,
        "}",
    ]
    return " ".join(segment for segment in segments if segment)


def _build_nsjail_config(
    *,
    config_data: LocalMCPDiscoveryConfig,
    job_dir: Path,
    command_path: str,
    args: list[str],
    timeout_seconds: int,
    home_dir: Path,
    uv_cache_dir: Path,
    npm_cache_dir: Path,
    policy: _ResolvedEgressPolicy,
    hosts_resolution_files: tuple[Path, Path, Path] | None,
) -> str:
    rootfs = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    network_enabled = config_data.allow_network
    lines = [
        'name: "mcp_stdio_discovery"',
        "mode: ONCE",
        'hostname: "mcp-discovery"',
        "keep_env: false",
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
        f"mount {{ src: {_quote_nsjail_string(str(rootfs / 'usr'))} dst: {_quote_nsjail_string('/usr')} is_bind: true rw: false }}",
        f"mount {{ src: {_quote_nsjail_string(str(rootfs / 'lib'))} dst: {_quote_nsjail_string('/lib')} is_bind: true rw: false }}",
        f"mount {{ src: {_quote_nsjail_string(str(rootfs / 'bin'))} dst: {_quote_nsjail_string('/bin')} is_bind: true rw: false }}",
        f"mount {{ src: {_quote_nsjail_string(str(rootfs / 'etc'))} dst: {_quote_nsjail_string('/etc')} is_bind: true rw: false }}",
    ]
    for optional_mount in ("lib64", "sbin"):
        optional_path = rootfs / optional_mount
        if optional_path.exists():
            lines.append(
                f"mount {{ src: {_quote_nsjail_string(str(optional_path))} dst: {_quote_nsjail_string(f'/{optional_mount}')} is_bind: true rw: false }}"
            )
    if network_enabled:
        if hosts_resolution_files is None:
            lines.extend(
                [
                    "",
                    "# DNS config - use host resolver files directly",
                    'mount { src: "/etc/resolv.conf" dst: "/etc/resolv.conf" is_bind: true rw: false }',
                    'mount { src: "/etc/hosts" dst: "/etc/hosts" is_bind: true rw: false }',
                    'mount { src: "/etc/nsswitch.conf" dst: "/etc/nsswitch.conf" is_bind: true rw: false }',
                ]
            )
        else:
            hosts_path, nsswitch_path, resolv_path = hosts_resolution_files
            lines.extend(
                [
                    "",
                    "# DNS config - use job-local hosts-only resolution for allowlisted hosts",
                    f"mount {{ src: {_quote_nsjail_string(str(resolv_path))} dst: {_quote_nsjail_string('/etc/resolv.conf')} is_bind: true rw: false }}",
                    f"mount {{ src: {_quote_nsjail_string(str(hosts_path))} dst: {_quote_nsjail_string('/etc/hosts')} is_bind: true rw: false }}",
                    f"mount {{ src: {_quote_nsjail_string(str(nsswitch_path))} dst: {_quote_nsjail_string('/etc/nsswitch.conf')} is_bind: true rw: false }}",
                ]
            )
    exec_line = _build_exec_bin_line(
        command_path=command_path,
        args=args,
        policy=policy,
    )

    lines.extend(
        [
            "",
            "# /proc - read-only bind mount",
            'mount { src: "/proc" dst: "/proc" is_bind: true rw: false }',
            "",
            "# /dev essentials",
            'mount { src: "/dev/null" dst: "/dev/null" is_bind: true rw: true }',
            'mount { src: "/dev/urandom" dst: "/dev/urandom" is_bind: true rw: false }',
            'mount { src: "/dev/random" dst: "/dev/random" is_bind: true rw: false }',
            'mount { src: "/dev/zero" dst: "/dev/zero" is_bind: true rw: false }',
            "",
            "# Writable directories",
            'mount { dst: "/tmp" fstype: "tmpfs" rw: true options: "size=256M" }',
            f"mount {{ src: {_quote_nsjail_string(str(job_dir))} dst: {_quote_nsjail_string('/work')} is_bind: true rw: true }}",
            f"mount {{ src: {_quote_nsjail_string(str(home_dir))} dst: {_quote_nsjail_string(str(_JAILED_HOME_DIR))} is_bind: true rw: true }}",
            f"mount {{ src: {_quote_nsjail_string(str(uv_cache_dir))} dst: {_quote_nsjail_string(str(_JAILED_UV_CACHE_DIR))} is_bind: true rw: true }}",
            f"mount {{ src: {_quote_nsjail_string(str(npm_cache_dir))} dst: {_quote_nsjail_string(str(_JAILED_NPM_CACHE_DIR))} is_bind: true rw: true }}",
            "",
            "# Resource limits",
            f"rlimit_as: {config.TRACECAT__AGENT_SANDBOX_MEMORY_MB * 1024 * 1024}",
            f"rlimit_cpu: {timeout_seconds}",
            "rlimit_fsize: 268435456",
            "rlimit_nofile: 512",
            "rlimit_nproc: 128",
            f"time_limit: {timeout_seconds}",
            "",
            "# Execution - launch the MCP stdio server directly",
            f"cwd: {_quote_nsjail_string('/work')}",
            exec_line,
        ]
    )
    return "\n".join(lines)


async def _build_stdio_transport(
    *,
    config_data: LocalMCPDiscoveryConfig,
    job_dir: Path,
    stderr_path: Path,
    timeout_seconds: int,
    uv_cache_dir: Path,
    npm_cache_dir: Path,
    home_dir: Path,
) -> StdioTransport:
    server = config_data.server
    has_egress_policy = bool(
        config_data.egress_allowlist or config_data.egress_denylist
    )
    requires_network_isolation = not config_data.allow_network or has_egress_policy
    policy = await _resolve_egress_policy(config_data)
    hosts_resolution_files = _write_hosts_resolution_files(
        policy=policy,
        job_dir=job_dir,
    )
    direct_env = _build_runtime_env(
        config_data=config_data,
        home_dir=home_dir,
        uv_cache_dir=uv_cache_dir,
        npm_cache_dir=npm_cache_dir,
        jailed=False,
    )
    if not is_nsjail_available():
        if requires_network_isolation:
            raise _config_validation_error(
                "Local MCP discovery requires nsjail to enforce network policy.",
                allow_network=config_data.allow_network,
            )
        return StdioTransport(
            command=server["command"],
            args=server.get("args", []),
            env=direct_env,
            cwd=str(job_dir),
            keep_alive=False,
            log_file=stderr_path,
        )

    command = server["command"]
    command_path = _resolve_command_path(command)
    if command_path is None:
        if requires_network_isolation:
            raise _config_validation_error(
                "Local MCP discovery requires an nsjail-visible command path to enforce network policy.",
                command=command,
            )
        logger.warning(
            "Falling back to direct subprocess for local MCP discovery; command path is not nsjail-visible",
            command=command,
        )
        return StdioTransport(
            command=command,
            args=server.get("args", []),
            env=direct_env,
            cwd=str(job_dir),
            keep_alive=False,
            log_file=stderr_path,
        )

    jailed_env = _build_runtime_env(
        config_data=config_data,
        home_dir=home_dir,
        uv_cache_dir=uv_cache_dir,
        npm_cache_dir=npm_cache_dir,
        jailed=True,
    )
    nsjail_config = _build_nsjail_config(
        config_data=config_data,
        job_dir=job_dir,
        command_path=command_path,
        args=server.get("args", []),
        timeout_seconds=timeout_seconds,
        home_dir=home_dir,
        uv_cache_dir=uv_cache_dir,
        npm_cache_dir=npm_cache_dir,
        policy=policy,
        hosts_resolution_files=hosts_resolution_files,
    )
    config_path = job_dir / "nsjail.cfg"
    config_path.write_text(nsjail_config, encoding="utf-8")
    config_path.chmod(0o600)
    env_args: list[str] = []
    for key in sorted(jailed_env):
        env_args.extend(["--env", key])
    return StdioTransport(
        command=config.TRACECAT__SANDBOX_NSJAIL_PATH,
        args=["--config", str(config_path), *env_args],
        env=jailed_env,
        cwd=str(job_dir),
        keep_alive=False,
        log_file=stderr_path,
    )


async def discover_local_mcp_server_catalog(
    config_data: LocalMCPDiscoveryConfig,
) -> MCPServerCatalog:
    """Discover normalized tools/resources/prompts for a local stdio MCP server."""
    command = config_data.server["command"]
    args = config_data.server.get("args", [])
    env = config_data.server.get("env")

    try:
        validate_mcp_command_config(command=command, args=args, env=env)
    except Exception as exc:
        raise LocalMCPDiscoveryError(
            phase=LocalMCPDiscoveryPhase.CONFIG_VALIDATION,
            summary=_phase_summary(LocalMCPDiscoveryPhase.CONFIG_VALIDATION),
            details={"message": str(exc)},
        ) from exc

    timeout_seconds = (
        config_data.timeout_seconds or config_data.server.get("timeout") or 60
    )
    semaphore = _get_local_sandbox_semaphore()
    job_dir = Path(tempfile.mkdtemp(prefix="mcp-local-discovery-"))
    stderr_path = job_dir / "stderr.log"
    uv_cache_dir, npm_cache_dir, home_dir = _prepare_runtime_dirs(
        config_data=config_data,
        job_dir=job_dir,
    )

    try:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    _discover_local_mcp_server_catalog_inner(
                        config_data=config_data,
                        job_dir=job_dir,
                        stderr_path=stderr_path,
                        timeout_seconds=timeout_seconds,
                        uv_cache_dir=uv_cache_dir,
                        npm_cache_dir=npm_cache_dir,
                        home_dir=home_dir,
                    ),
                    timeout=timeout_seconds,
                )
            except TimeoutError as exc:
                raise LocalMCPDiscoveryError(
                    phase=LocalMCPDiscoveryPhase.TIMEOUT,
                    summary=_phase_summary(LocalMCPDiscoveryPhase.TIMEOUT),
                    details={"timeout_seconds": timeout_seconds},
                ) from exc
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


async def _discover_local_mcp_server_catalog_inner(
    *,
    config_data: LocalMCPDiscoveryConfig,
    job_dir: Path,
    stderr_path: Path,
    timeout_seconds: int,
    uv_cache_dir: Path,
    npm_cache_dir: Path,
    home_dir: Path,
) -> MCPServerCatalog:
    server = config_data.server
    initialized = False
    transport = await _build_stdio_transport(
        config_data=config_data,
        job_dir=job_dir,
        stderr_path=stderr_path,
        timeout_seconds=timeout_seconds,
        uv_cache_dir=uv_cache_dir,
        npm_cache_dir=npm_cache_dir,
        home_dir=home_dir,
    )

    try:
        async with Client(
            transport,
            timeout=timeout_seconds,
        ) as client:
            initialized = True
            try:
                tools = tuple(
                    normalize_tool(tool) for tool in await client.list_tools()
                )
            except Exception as exc:
                raise LocalMCPDiscoveryError(
                    phase=LocalMCPDiscoveryPhase.LIST_TOOLS,
                    summary=_phase_summary(LocalMCPDiscoveryPhase.LIST_TOOLS),
                    details={"message": str(exc)},
                ) from exc

            try:
                resources = tuple(
                    normalize_resource(resource)
                    for resource in await list_optional_capability(
                        server_name=server["name"],
                        capability_name="resources",
                        list_fn=client.list_resources,
                    )
                )
            except Exception as exc:
                raise LocalMCPDiscoveryError(
                    phase=LocalMCPDiscoveryPhase.LIST_RESOURCES,
                    summary=_phase_summary(LocalMCPDiscoveryPhase.LIST_RESOURCES),
                    details={"message": str(exc)},
                ) from exc

            try:
                prompts = tuple(
                    normalize_prompt(prompt)
                    for prompt in await list_optional_capability(
                        server_name=server["name"],
                        capability_name="prompts",
                        list_fn=client.list_prompts,
                    )
                )
            except Exception as exc:
                raise LocalMCPDiscoveryError(
                    phase=LocalMCPDiscoveryPhase.LIST_PROMPTS,
                    summary=_phase_summary(LocalMCPDiscoveryPhase.LIST_PROMPTS),
                    details={"message": str(exc)},
                ) from exc
    except LocalMCPDiscoveryError:
        raise
    except FileNotFoundError as exc:
        raise LocalMCPDiscoveryError(
            phase=LocalMCPDiscoveryPhase.PROCESS_SPAWN,
            summary=_phase_summary(LocalMCPDiscoveryPhase.PROCESS_SPAWN),
            details={"message": str(exc)},
        ) from exc
    except Exception as exc:
        phase = (
            _classify_connect_failure(
                command=server["command"],
                stderr_path=stderr_path,
            )
            if not initialized
            else LocalMCPDiscoveryPhase.SHUTDOWN
        )
        raise LocalMCPDiscoveryError(
            phase=phase,
            summary=_phase_summary(phase),
            details={
                "message": str(exc),
                "stderr": _stderr_tail(stderr_path),
            },
        ) from exc

    logger.debug(
        "Discovered local MCP server catalog",
        server_name=server["name"],
        tool_count=len(tools),
        resource_count=len(resources),
        prompt_count=len(prompts),
        allow_network=config_data.allow_network,
    )
    return MCPServerCatalog(
        server_name=server["name"],
        tools=tools,
        resources=resources,
        prompts=prompts,
    )
