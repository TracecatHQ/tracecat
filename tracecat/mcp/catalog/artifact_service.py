"""Persisted MCP catalog artifact resolution and remote execution."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import orjson
from sqlalchemy import select

from tracecat.agent.common.types import MCPHttpServerConfig
from tracecat.agent.mcp.user_client import UserMCPClient, infer_transport_type
from tracecat.db.models import (
    MCPIntegration,
    MCPIntegrationCatalogEntry,
    OAuthIntegration,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers import WorkspaceID
from tracecat.integrations.enums import MCPAuthType, MCPCatalogArtifactType
from tracecat.integrations.mcp_scopes import build_mcp_scope_name
from tracecat.integrations.service import IntegrationService
from tracecat.mcp.catalog.types import (
    MCPCatalogPromptResult,
    MCPCatalogResolvedArtifact,
    MCPCatalogResourceResult,
    MCPCatalogToolResult,
)
from tracecat.mcp.config import TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS
from tracecat.mcp.policy.service import MCPCatalogPolicyService
from tracecat.service import BaseOrgService


def _sanitize_remote_endpoint_for_log(server_uri: str | None) -> str | None:
    if not server_uri:
        return server_uri
    parsed = urlsplit(server_uri)
    if parsed.hostname is None:
        return server_uri.split("?", 1)[0].rsplit("@", 1)[-1]
    try:
        port = parsed.port
    except ValueError:
        return server_uri.split("?", 1)[0].rsplit("@", 1)[-1]

    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


class MCPCatalogArtifactService(BaseOrgService):
    """Resolve and execute persisted MCP catalog artifacts for wrapper operations."""

    service_name = "mcp_catalog_artifact"

    async def execute_tool(
        self,
        *,
        workspace_id: WorkspaceID,
        artifact_ref_or_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPCatalogToolResult:
        """Execute a persisted MCP tool artifact."""
        target = await self.resolve_artifact(
            workspace_id=workspace_id,
            artifact_ref_or_id=artifact_ref_or_id,
            artifact_type=MCPCatalogArtifactType.TOOL,
        )
        client = await self._create_remote_client(target)
        try:
            result = await client.call_tool_result(
                str(target.mcp_integration_id),
                target.artifact_ref,
                arguments or {},
            )
        except Exception as exc:
            self.logger.error(
                "Remote MCP tool execution failed",
                mcp_integration_id=target.mcp_integration_id,
                catalog_entry_id=target.id,
                artifact_type=target.artifact_type.value,
                remote_endpoint=_sanitize_remote_endpoint_for_log(target.server_uri),
                error_type=type(exc).__name__,
            )
            raise
        return MCPCatalogToolResult(
            workspace_id=workspace_id,
            artifact=target,
            result=cast(Any, result).model_dump(mode="json"),
        )

    async def read_resource(
        self,
        *,
        workspace_id: WorkspaceID,
        artifact_ref_or_id: str,
    ) -> MCPCatalogResourceResult:
        """Read a persisted MCP resource artifact with wrapper-side truncation."""
        target = await self.resolve_artifact(
            workspace_id=workspace_id,
            artifact_ref_or_id=artifact_ref_or_id,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
        )
        client = await self._create_remote_client(target)
        try:
            contents = await client.read_resource(
                str(target.mcp_integration_id),
                target.artifact_ref,
            )
        except Exception as exc:
            self.logger.error(
                "Remote MCP resource read failed",
                mcp_integration_id=target.mcp_integration_id,
                catalog_entry_id=target.id,
                artifact_type=target.artifact_type.value,
                remote_endpoint=_sanitize_remote_endpoint_for_log(target.server_uri),
                error_type=type(exc).__name__,
            )
            raise

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
                        payload["text"] = text[
                            :TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS
                        ]
                        payload["truncated"] = True
                        payload["original_length"] = original_length
                        truncated = True
                case {"blob": str(blob)}:
                    original_length = len(blob)
                    total_chars += original_length
                    if original_length > TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS:
                        payload["blob"] = blob[
                            :TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS
                        ]
                        payload["truncated"] = True
                        payload["original_length"] = original_length
                        truncated = True
            serialized_contents.append(payload)

        return MCPCatalogResourceResult(
            workspace_id=workspace_id,
            artifact=target,
            contents=tuple(serialized_contents),
            truncated=truncated,
            max_content_chars=TRACECAT_MCP__MAX_RESOURCE_CONTENT_CHARS,
            total_content_chars=total_chars,
        )

    async def get_prompt(
        self,
        *,
        workspace_id: WorkspaceID,
        artifact_ref_or_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPCatalogPromptResult:
        """Read a persisted MCP prompt artifact."""
        target = await self.resolve_artifact(
            workspace_id=workspace_id,
            artifact_ref_or_id=artifact_ref_or_id,
            artifact_type=MCPCatalogArtifactType.PROMPT,
        )
        client = await self._create_remote_client(target)
        try:
            result = await client.get_prompt(
                str(target.mcp_integration_id),
                target.artifact_ref,
                arguments,
            )
        except Exception as exc:
            self.logger.error(
                "Remote MCP prompt fetch failed",
                mcp_integration_id=target.mcp_integration_id,
                catalog_entry_id=target.id,
                artifact_type=target.artifact_type.value,
                remote_endpoint=_sanitize_remote_endpoint_for_log(target.server_uri),
                error_type=type(exc).__name__,
            )
            raise
        return MCPCatalogPromptResult(
            workspace_id=workspace_id,
            artifact=target,
            result=cast(Any, result).model_dump(mode="json"),
        )

    async def resolve_artifact(
        self,
        *,
        workspace_id: WorkspaceID,
        artifact_ref_or_id: str,
        artifact_type: MCPCatalogArtifactType,
    ) -> MCPCatalogResolvedArtifact:
        """Resolve a single authorized persisted artifact by UUID or exact ref."""
        policy_service = MCPCatalogPolicyService(session=self.session, role=self.role)
        if maybe_uuid := self._parse_uuid(artifact_ref_or_id):
            authorized_entry = await policy_service.authorize_catalog_entry(
                workspace_id=workspace_id,
                entry_id=maybe_uuid,
            )
            if authorized_entry.artifact_type != artifact_type:
                raise TracecatNotFoundError("MCP catalog entry not found")
            stmt = self._base_artifact_stmt().where(
                MCPIntegrationCatalogEntry.id == authorized_entry.id,
                MCPIntegrationCatalogEntry.workspace_id == workspace_id,
            )
            result = await self.session.execute(stmt)
            row = result.tuples().first()
            if row is None:
                raise TracecatNotFoundError("MCP catalog entry not found")
            return self._row_to_target(row)

        authorization = await policy_service.authorize_catalog_search(
            workspace_id=workspace_id
        )
        if not authorization.allowed_entry_ids:
            raise TracecatNotFoundError("MCP catalog entry not found")

        stmt = (
            self._base_artifact_stmt()
            .where(
                MCPIntegrationCatalogEntry.workspace_id == workspace_id,
                MCPIntegrationCatalogEntry.is_active.is_(True),
                MCPIntegrationCatalogEntry.id.in_(authorization.allowed_entry_ids),
                MCPIntegrationCatalogEntry.artifact_type == artifact_type.value,
                MCPIntegrationCatalogEntry.artifact_ref == artifact_ref_or_id,
            )
            .order_by(MCPIntegrationCatalogEntry.id)
        )
        result = await self.session.execute(stmt)
        rows = result.tuples().all()
        if not rows:
            raise TracecatNotFoundError("MCP catalog entry not found")
        if len(rows) > 1:
            raise TracecatValidationError(
                "Artifact reference is ambiguous; use the MCP catalog entry ID instead"
            )
        return self._row_to_target(rows[0])

    def _base_artifact_stmt(self) -> Any:
        return select(
            MCPIntegrationCatalogEntry.id,
            MCPIntegrationCatalogEntry.mcp_integration_id,
            MCPIntegrationCatalogEntry.workspace_id,
            MCPIntegrationCatalogEntry.artifact_type,
            MCPIntegrationCatalogEntry.artifact_key,
            MCPIntegrationCatalogEntry.artifact_ref,
            MCPIntegrationCatalogEntry.display_name,
            MCPIntegrationCatalogEntry.description,
            MCPIntegration.scope_namespace,
            MCPIntegration.server_type,
            MCPIntegration.server_uri,
            MCPIntegration.auth_type,
            MCPIntegration.oauth_integration_id,
            MCPIntegration.encrypted_headers,
            MCPIntegration.timeout,
        ).join(
            MCPIntegration,
            MCPIntegration.id == MCPIntegrationCatalogEntry.mcp_integration_id,
        )

    def _row_to_target(self, row: Sequence[Any]) -> MCPCatalogResolvedArtifact:
        (
            entry_id,
            mcp_integration_id,
            workspace_id,
            artifact_type_value,
            artifact_key,
            artifact_ref,
            display_name,
            description,
            scope_namespace,
            server_type,
            server_uri,
            auth_type,
            oauth_integration_id,
            encrypted_headers,
            timeout,
        ) = row
        artifact_type = MCPCatalogArtifactType(artifact_type_value)
        scope_name, _resource, _action = build_mcp_scope_name(
            scope_namespace=scope_namespace,
            artifact_type=artifact_type,
            artifact_key=artifact_key,
        )
        return MCPCatalogResolvedArtifact(
            id=entry_id,
            mcp_integration_id=mcp_integration_id,
            workspace_id=workspace_id,
            artifact_type=artifact_type,
            artifact_key=artifact_key,
            artifact_ref=artifact_ref,
            display_name=display_name,
            description=description,
            scope_name=scope_name,
            server_type=server_type,
            server_uri=server_uri,
            auth_type=auth_type,
            oauth_integration_id=oauth_integration_id,
            encrypted_headers=encrypted_headers,
            timeout=timeout,
        )

    async def _create_remote_client(
        self, target: MCPCatalogResolvedArtifact
    ) -> UserMCPClient:
        if target.server_type != "http":
            raise TracecatValidationError(
                "Local stdio MCP artifacts are not available through this wrapper yet"
            )
        if not target.server_uri:
            raise TracecatValidationError("MCP integration server URI is missing")
        headers = await self._build_headers(target)
        config: MCPHttpServerConfig = {
            "type": "http",
            "name": str(target.mcp_integration_id),
            "url": target.server_uri,
            "transport": infer_transport_type(target.server_uri),
        }
        if headers:
            config["headers"] = headers
        if target.timeout is not None:
            config["timeout"] = target.timeout
        return UserMCPClient([config])

    async def _build_headers(
        self, target: MCPCatalogResolvedArtifact
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        integration_service = IntegrationService(session=self.session, role=self.role)
        if target.encrypted_headers:
            if decrypted := integration_service._decrypt_token(
                target.encrypted_headers
            ):
                loaded = orjson.loads(decrypted)
                if not isinstance(loaded, dict):
                    raise TracecatValidationError(
                        "Stored MCP integration headers must decode to an object"
                    )
                for key, value in loaded.items():
                    if not isinstance(key, str) or not isinstance(value, str):
                        raise TracecatValidationError(
                            "Stored MCP integration headers must be string pairs"
                        )
                    headers[key] = value

        if (
            target.auth_type == MCPAuthType.OAUTH2
            and target.oauth_integration_id is not None
        ):
            oauth_integration = await self.session.get(
                OAuthIntegration, target.oauth_integration_id
            )
            if oauth_integration is None:
                raise TracecatNotFoundError("OAuth integration not found")
            oauth_integration = await integration_service.refresh_token_if_needed(
                oauth_integration
            )
            access_token = await integration_service.get_access_token(oauth_integration)
            if access_token is None:
                raise TracecatValidationError(
                    "OAuth access token is not available for MCP integration"
                )
            if auth_header_keys := [
                key for key in headers if key.strip().casefold() == "authorization"
            ]:
                for auth_header_key in auth_header_keys:
                    headers.pop(auth_header_key, None)
            token_type = oauth_integration.token_type or "Bearer"
            headers["Authorization"] = f"{token_type} {access_token.get_secret_value()}"
        return headers

    @staticmethod
    def _parse_uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except ValueError:
            return None
