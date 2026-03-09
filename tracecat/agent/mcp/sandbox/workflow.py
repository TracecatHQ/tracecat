"""Temporal workflow and activity for local stdio MCP artifact execution."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import orjson
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from fastmcp import Client
    from mcp.types import BlobResourceContents, GetPromptResult, TextResourceContents

    from tracecat import config
    from tracecat.agent.common.exceptions import AgentSandboxValidationError
    from tracecat.agent.mcp.sandbox.types import (
        LocalMCPArtifactOperation,
        RunLocalMCPArtifactWorkflowInput,
        RunLocalMCPArtifactWorkflowResult,
    )
    from tracecat.agent.sandbox.config import _contains_dangerous_chars, _validate_path
    from tracecat.integrations.service import IntegrationService
    from tracecat.logger import logger
    from tracecat.mcp.catalog.artifact_service import MCPCatalogArtifactService
    from tracecat.mcp.config import TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS

_LOCAL_MCP_SANDBOX_SEMAPHORE = asyncio.Semaphore(
    config.TRACECAT__MCP_MAX_CONCURRENT_LOCAL_SANDBOXES
)
_DIRECT_SANDBOX_WARNING_EMITTED = False
_EGRESS_POLICY_DIRECT_WARNING_EMITTED = False


def _cache_env(
    *,
    organization_id: Any,
    base_env: dict[str, str] | None,
) -> dict[str, str]:
    """Inject shared per-org cache paths for package managers."""
    cache_root = Path(config.TRACECAT__MCP_SANDBOX_CACHE_DIR) / str(organization_id)
    xdg_cache = cache_root / "xdg"
    npm_cache = cache_root / "npm"
    pip_cache = cache_root / "pip"
    uv_cache = cache_root / "uv"
    home_dir = cache_root / "home"
    for path in (cache_root, xdg_cache, npm_cache, pip_cache, uv_cache, home_dir):
        path.mkdir(parents=True, exist_ok=True)

    env = dict(base_env or {})
    env.setdefault("HOME", str(home_dir))
    env["XDG_CACHE_HOME"] = str(xdg_cache)
    env["npm_config_cache"] = str(npm_cache)
    env["PIP_CACHE_DIR"] = str(pip_cache)
    env["UV_CACHE_DIR"] = str(uv_cache)
    return env


def _truncate_resource_contents(
    contents: list[TextResourceContents | BlobResourceContents],
) -> tuple[tuple[dict[str, Any], ...], bool, int]:
    total_chars = 0
    truncated = False
    serialized_contents: list[dict[str, Any]] = []
    for content in contents:
        payload = content.model_dump(mode="json")
        match payload:
            case {"text": str(text)}:
                original_length = len(text)
                total_chars += original_length
                if original_length > TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS:
                    payload["text"] = text[:TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS]
                    payload["truncated"] = True
                    payload["original_length"] = original_length
                    truncated = True
            case {"blob": str(blob)}:
                original_length = len(blob)
                total_chars += original_length
                if original_length > TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS:
                    payload["blob"] = blob[:TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS]
                    payload["truncated"] = True
                    payload["original_length"] = original_length
                    truncated = True
        serialized_contents.append(payload)
    return tuple(serialized_contents), truncated, total_chars


def _resolve_stdio_command_path(command: str, env: dict[str, str]) -> str | None:
    """Resolve a stdio command to an absolute executable path."""
    if os.path.isabs(command):
        return command if Path(command).exists() else None
    return shutil.which(command, path=env.get("PATH"))


def _is_supported_nsjail_command_path(command_path: str) -> bool:
    """Only use nsjail when the executable path is covered by mounted rootfs paths."""
    supported_prefixes = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/")
    return command_path.startswith(supported_prefixes)


def _validate_stdio_nsjail_arg(arg: str, index: int) -> None:
    """Reject stdio args that could break protobuf text-format parsing."""
    is_dangerous, reason = _contains_dangerous_chars(arg)
    if is_dangerous:
        raise AgentSandboxValidationError(
            f"Invalid stdio arg at index {index}: {reason}"
        )

    dangerous_chars = ('"', "'", "\\", "{", "}", "\n", "\r", "\t")
    if found_chars := [char for char in dangerous_chars if char in arg]:
        raise AgentSandboxValidationError(
            f"Invalid stdio arg at index {index}: contains dangerous characters {found_chars!r}"
        )


def _validate_stdio_nsjail_args(command_args: list[str]) -> tuple[str, ...]:
    """Validate stdio args before interpolating them into nsjail config."""
    for index, arg in enumerate(command_args):
        if not isinstance(arg, str):
            raise AgentSandboxValidationError(
                f"Invalid stdio arg at index {index}: expected str, got {type(arg).__name__}"
            )
        _validate_stdio_nsjail_arg(arg, index)
    return tuple(command_args)


def _build_stdio_nsjail_config(
    *,
    command_path: str,
    command_args: list[str],
    cache_root: Path,
    rootfs: Path,
    allow_network: bool,
) -> str:
    """Build a minimal nsjail config for one stdio MCP process."""
    _validate_path(rootfs, "rootfs")
    _validate_path(cache_root, "cache_root")
    _validate_path(Path(command_path), "command_path")
    validated_command_args = _validate_stdio_nsjail_args(command_args)

    clone_newnet = not allow_network
    lines = [
        'name: "local_mcp_stdio"',
        "mode: ONCE",
        'hostname: "mcp"',
        "keep_env: false",
        "",
        "# Namespace isolation",
        f"clone_newnet: {'true' if clone_newnet else 'false'}",
        "clone_newuser: true",
        "clone_newns: true",
        "clone_newpid: true",
        "clone_newipc: true",
        "clone_newuts: true",
        "",
        "# UID/GID mapping",
        f'uidmap {{ inside_id: "1000" outside_id: "{os.getuid()}" count: 1 }}',
        f'gidmap {{ inside_id: "1000" outside_id: "{os.getgid()}" count: 1 }}',
        "",
        "# Rootfs mounts",
        f'mount {{ src: "{rootfs}/usr" dst: "/usr" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/lib" dst: "/lib" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/bin" dst: "/bin" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/etc" dst: "/etc" is_bind: true rw: false }}',
    ]

    for optional_dir in ("lib64", "sbin"):
        optional_path = rootfs / optional_dir
        if optional_path.exists():
            lines.append(
                f'mount {{ src: "{optional_path}" dst: "/{optional_dir}" is_bind: true rw: false }}'
            )

    if allow_network:
        lines.extend(
            [
                "",
                "# DNS config",
                'mount { src: "/etc/resolv.conf" dst: "/etc/resolv.conf" is_bind: true rw: false }',
                'mount { src: "/etc/hosts" dst: "/etc/hosts" is_bind: true rw: false }',
                'mount { src: "/etc/nsswitch.conf" dst: "/etc/nsswitch.conf" is_bind: true rw: false }',
            ]
        )

    lines.extend(
        [
            "",
            "# Runtime mounts",
            'mount { src: "/proc" dst: "/proc" is_bind: true rw: false }',
            'mount { src: "/dev/null" dst: "/dev/null" is_bind: true rw: true }',
            'mount { src: "/dev/urandom" dst: "/dev/urandom" is_bind: true rw: false }',
            'mount { src: "/dev/random" dst: "/dev/random" is_bind: true rw: false }',
            'mount { src: "/dev/zero" dst: "/dev/zero" is_bind: true rw: false }',
            'mount { dst: "/tmp" fstype: "tmpfs" rw: true options: "size=256M" }',
            f'mount {{ src: "{cache_root}" dst: "/cache" is_bind: true rw: true }}',
            "",
            "# Resource limits",
            f"rlimit_as: {2048 * 1024 * 1024}",
            "rlimit_cpu: 300",
            f"time_limit: {300}",
            "",
            "# Execution",
            'cwd: "/cache/home"',
        ]
    )

    exec_parts = [f'exec_bin {{ path: "{command_path}"']
    for arg in validated_command_args:
        exec_parts.append(f' arg: "{arg}"')
    exec_parts.append(" }")
    lines.append("".join(exec_parts))
    return "\n".join(lines)


def _build_stdio_client_config(
    *,
    target: Any,
    stdio_env: dict[str, str],
) -> tuple[dict[str, Any], Path | None]:
    """Build a FastMCP stdio client config, using nsjail when available."""
    global _DIRECT_SANDBOX_WARNING_EMITTED, _EGRESS_POLICY_DIRECT_WARNING_EMITTED

    direct_config = {
        "transport": "stdio",
        "command": target.stdio_command,
        "args": target.stdio_args or [],
        "env": stdio_env,
    }
    if target.timeout is not None:
        direct_config["timeout"] = target.timeout

    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    if (
        config.TRACECAT__DISABLE_NSJAIL
        or not nsjail_path.exists()
        or not rootfs_path.exists()
    ):
        if not _DIRECT_SANDBOX_WARNING_EMITTED and (
            not target.sandbox_allow_network
            or target.sandbox_egress_allowlist
            or target.sandbox_egress_denylist
        ):
            logger.warning(
                "Network isolation is not enforced without nsjail; local MCP process will run unsandboxed",
                mcp_integration_id=target.mcp_integration_id,
            )
            _DIRECT_SANDBOX_WARNING_EMITTED = True
        if not _EGRESS_POLICY_DIRECT_WARNING_EMITTED and (
            target.sandbox_egress_allowlist or target.sandbox_egress_denylist
        ):
            logger.warning(
                "Egress allowlist/denylist is not enforced without nsjail; local MCP process will use degraded policy mode",
                mcp_integration_id=target.mcp_integration_id,
            )
            _EGRESS_POLICY_DIRECT_WARNING_EMITTED = True
        return direct_config, None

    command_path = _resolve_stdio_command_path(target.stdio_command, stdio_env)
    if command_path is None or not _is_supported_nsjail_command_path(command_path):
        logger.warning(
            "Falling back to direct local MCP execution because stdio command path cannot be jailed",
            mcp_integration_id=target.mcp_integration_id,
            command=target.stdio_command,
            resolved_command=command_path,
        )
        return direct_config, None

    cache_root = Path(stdio_env["HOME"]).parent
    nsjail_job_dir = Path(tempfile.mkdtemp(prefix="local-mcp-nsjail-"))
    nsjail_cfg = _build_stdio_nsjail_config(
        command_path=command_path,
        command_args=target.stdio_args or [],
        cache_root=cache_root,
        rootfs=rootfs_path,
        allow_network=target.sandbox_allow_network,
    )
    config_path = nsjail_job_dir / "nsjail.cfg"
    config_path.write_text(nsjail_cfg)
    config_path.chmod(0o600)

    nsjail_env = {
        "PATH": stdio_env.get(
            "PATH", os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        ),
        "HOME": "/cache/home",
        "USER": "agent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        **stdio_env,
    }
    nsjail_env["HOME"] = "/cache/home"
    if target.sandbox_egress_allowlist:
        nsjail_env["TRACECAT__MCP_SANDBOX_EGRESS_ALLOWLIST"] = orjson.dumps(
            target.sandbox_egress_allowlist
        ).decode()
    if target.sandbox_egress_denylist:
        nsjail_env["TRACECAT__MCP_SANDBOX_EGRESS_DENYLIST"] = orjson.dumps(
            target.sandbox_egress_denylist
        ).decode()
    if target.sandbox_egress_allowlist or target.sandbox_egress_denylist:
        nsjail_env["LD_PRELOAD"] = "/usr/local/lib/libtracecat_mcp_egress_guard.so"

    nsjail_args = ["--config", str(config_path)]
    for key in sorted(nsjail_env):
        nsjail_args.extend(["--env", key])

    return {
        "transport": "stdio",
        "command": str(nsjail_path),
        "args": nsjail_args,
        "env": nsjail_env,
        "timeout": target.timeout,
    }, nsjail_job_dir


@workflow.defn
class RunLocalMCPArtifactWorkflow:
    """Execute one local stdio MCP artifact operation on the MCP queue."""

    @workflow.run
    async def run(
        self, request: RunLocalMCPArtifactWorkflowInput
    ) -> RunLocalMCPArtifactWorkflowResult:
        return await workflow.execute_activity(
            run_local_mcp_artifact_activity,
            request,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def run_local_mcp_artifact_activity(
    request: RunLocalMCPArtifactWorkflowInput,
) -> RunLocalMCPArtifactWorkflowResult:
    """Execute one local stdio MCP artifact call in an ephemeral subprocess."""
    async with _LOCAL_MCP_SANDBOX_SEMAPHORE:
        async with MCPCatalogArtifactService.with_session(role=request.role) as service:
            target = await service.resolve_artifact(
                workspace_id=request.workspace_id,
                artifact_ref_or_id=request.artifact_ref_or_id,
                artifact_type=request.artifact_type,
            )
            if target.server_type != "stdio":
                raise ValueError("Local MCP sandbox workflow requires a stdio artifact")
            if not target.stdio_command:
                raise ValueError("Local MCP integration is missing stdio_command")

            integration_service = IntegrationService(service.session, role=request.role)
            sandbox_phase = "resolve_env"
            stdio_env = integration_service.decrypt_marshaled_stdio_env(
                target.encrypted_stdio_env
            )
            if stdio_env:
                stdio_env = await integration_service.resolve_stdio_env(
                    stdio_env=stdio_env,
                    mcp_integration_id=target.mcp_integration_id,
                    mcp_integration_slug=target.integration_slug,
                )
            stdio_env = _cache_env(
                organization_id=request.role.organization_id,
                base_env=stdio_env,
            )

            server_config, temp_dir = _build_stdio_client_config(
                target=target,
                stdio_env=stdio_env,
            )
            try:
                sandbox_phase = "launch"
                async with Client(
                    {"mcpServers": {str(target.mcp_integration_id): server_config}},
                    timeout=target.timeout,
                ) as client:
                    sandbox_phase = "execute"
                    match request.operation:
                        case LocalMCPArtifactOperation.TOOL:
                            result = await client.call_tool(
                                target.artifact_ref, request.arguments or {}
                            )
                            return RunLocalMCPArtifactWorkflowResult(
                                result=cast(Any, result).model_dump(mode="json")
                            )
                        case LocalMCPArtifactOperation.RESOURCE:
                            contents = await client.read_resource(target.artifact_ref)
                            payload, truncated, total_chars = (
                                _truncate_resource_contents(
                                    cast(
                                        list[
                                            TextResourceContents | BlobResourceContents
                                        ],
                                        contents,
                                    )
                                )
                            )
                            return RunLocalMCPArtifactWorkflowResult(
                                contents=payload,
                                truncated=truncated,
                                max_content_chars=TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS,
                                total_content_chars=total_chars,
                            )
                        case LocalMCPArtifactOperation.PROMPT:
                            result = await client.get_prompt(
                                target.artifact_ref, request.arguments or None
                            )
                            return RunLocalMCPArtifactWorkflowResult(
                                result=cast(GetPromptResult, result).model_dump(
                                    mode="json"
                                )
                            )
            except Exception as exc:
                logger.error(
                    "Local MCP artifact execution failed",
                    mcp_integration_id=target.mcp_integration_id,
                    catalog_entry_id=target.id,
                    artifact_type=target.artifact_type.value,
                    sandbox_phase=sandbox_phase,
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)


class LocalMCPArtifactActivities:
    """Worker registration container for local MCP artifact activities."""

    @classmethod
    def get_activities(cls) -> list:
        return [run_local_mcp_artifact_activity]
