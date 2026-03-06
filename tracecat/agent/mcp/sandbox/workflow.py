"""Temporal workflow and activity for local stdio MCP artifact execution."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from fastmcp import Client
from mcp.types import BlobResourceContents, GetPromptResult, TextResourceContents
from temporalio import activity, workflow

from tracecat import config
from tracecat.agent.mcp.sandbox.types import (
    LocalMCPArtifactOperation,
    RunLocalMCPArtifactWorkflowInput,
    RunLocalMCPArtifactWorkflowResult,
)
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.mcp.catalog.artifact_service import MCPCatalogArtifactService
from tracecat.mcp.config import TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS

_LOCAL_MCP_SANDBOX_SEMAPHORE = asyncio.Semaphore(
    config.TRACECAT__MCP_MAX_CONCURRENT_LOCAL_SANDBOXES
)


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

            client_config = {
                "mcpServers": {
                    str(target.mcp_integration_id): {
                        "transport": "stdio",
                        "command": target.stdio_command,
                        "args": target.stdio_args or [],
                        "env": stdio_env,
                    }
                }
            }

            try:
                sandbox_phase = "launch"
                async with Client(client_config, timeout=target.timeout) as client:
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


class LocalMCPArtifactActivities:
    """Worker registration container for local MCP artifact activities."""

    @classmethod
    def get_activities(cls) -> list:
        return [run_local_mcp_artifact_activity]
