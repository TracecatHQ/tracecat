"""Authorization service for persisted MCP catalog entries."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from tracecat.auth.credentials import compute_effective_scopes
from tracecat.authz.controls import has_scope
from tracecat.db.models import MCPIntegration, MCPIntegrationCatalogEntry, Workspace
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.integrations.enums import MCPCatalogArtifactType
from tracecat.integrations.mcp_scopes import build_mcp_scope_name
from tracecat.mcp.policy.types import (
    AuthorizedMCPCatalogEntry,
    MCPCatalogAuthorizationResult,
)
from tracecat.service import BaseOrgService


class MCPCatalogPolicyService(BaseOrgService):
    """Resolve caller-specific authorization over persisted MCP catalog entries."""

    service_name = "mcp_catalog_policy"

    async def authorize_catalog_search(
        self,
        *,
        workspace_id: WorkspaceID,
    ) -> MCPCatalogAuthorizationResult:
        """Authorize catalog search/list operations for a workspace."""
        return await self._authorize_entries(workspace_id=workspace_id)

    async def authorize_catalog_entries(
        self,
        *,
        workspace_id: WorkspaceID,
        entry_ids: Sequence[UUID],
    ) -> MCPCatalogAuthorizationResult:
        """Authorize a batch of catalog entries for the current caller."""
        return await self._authorize_entries(
            workspace_id=workspace_id,
            entry_ids=frozenset(entry_ids),
        )

    async def authorize_catalog_entry(
        self,
        *,
        workspace_id: WorkspaceID,
        entry_id: UUID,
    ) -> AuthorizedMCPCatalogEntry:
        """Authorize a single catalog entry for the current caller."""
        result = await self._authorize_entries(
            workspace_id=workspace_id,
            entry_ids=frozenset({entry_id}),
        )
        if result.entries:
            return result.entries[0]

        exists_stmt = (
            select(MCPIntegrationCatalogEntry.id)
            .join(
                MCPIntegration,
                MCPIntegration.id == MCPIntegrationCatalogEntry.mcp_integration_id,
            )
            .join(Workspace, Workspace.id == MCPIntegrationCatalogEntry.workspace_id)
            .where(
                MCPIntegrationCatalogEntry.id == entry_id,
                MCPIntegrationCatalogEntry.workspace_id == workspace_id,
                MCPIntegrationCatalogEntry.is_active.is_(True),
                Workspace.organization_id == self.organization_id,
            )
        )
        exists_result = await self.session.execute(exists_stmt)
        if exists_result.scalar_one_or_none() is None:
            raise TracecatNotFoundError("MCP catalog entry not found")
        raise TracecatAuthorizationError("Not authorized to access MCP catalog entry")

    async def _authorize_entries(
        self,
        *,
        workspace_id: WorkspaceID,
        entry_ids: frozenset[UUID] | None = None,
    ) -> MCPCatalogAuthorizationResult:
        organization_id = await self._validate_workspace(workspace_id)
        effective_scopes = await self._get_effective_scopes()
        is_org_admin = has_scope(effective_scopes, "org:workspace:read")
        if entry_ids is not None and not entry_ids:
            return MCPCatalogAuthorizationResult(
                workspace_id=workspace_id,
                organization_id=organization_id,
                is_org_admin=is_org_admin,
                allowed_scope_names=frozenset(),
                entries=(),
            )

        stmt = (
            select(
                MCPIntegrationCatalogEntry.id,
                MCPIntegrationCatalogEntry.mcp_integration_id,
                MCPIntegrationCatalogEntry.workspace_id,
                MCPIntegrationCatalogEntry.artifact_type,
                MCPIntegrationCatalogEntry.artifact_key,
                MCPIntegrationCatalogEntry.artifact_ref,
                MCPIntegrationCatalogEntry.display_name,
                MCPIntegrationCatalogEntry.description,
                MCPIntegration.scope_namespace,
            )
            .join(
                MCPIntegration,
                MCPIntegration.id == MCPIntegrationCatalogEntry.mcp_integration_id,
            )
            .where(
                MCPIntegrationCatalogEntry.workspace_id == workspace_id,
                MCPIntegrationCatalogEntry.is_active.is_(True),
            )
            .order_by(
                MCPIntegrationCatalogEntry.artifact_type,
                MCPIntegrationCatalogEntry.artifact_key,
            )
        )
        if entry_ids is not None:
            stmt = stmt.where(MCPIntegrationCatalogEntry.id.in_(entry_ids))

        result = await self.session.execute(stmt)
        entries: list[AuthorizedMCPCatalogEntry] = []
        allowed_scope_names: set[str] = set()
        for (
            entry_id,
            mcp_integration_id,
            entry_workspace_id,
            artifact_type_value,
            artifact_key,
            artifact_ref,
            display_name,
            description,
            scope_namespace,
        ) in result.tuples().all():
            artifact_type = MCPCatalogArtifactType(artifact_type_value)
            scope_name, _resource, _action = build_mcp_scope_name(
                scope_namespace=scope_namespace,
                artifact_type=artifact_type,
                artifact_key=artifact_key,
            )
            if not is_org_admin and scope_name not in effective_scopes:
                continue
            allowed_scope_names.add(scope_name)
            entries.append(
                AuthorizedMCPCatalogEntry(
                    id=entry_id,
                    mcp_integration_id=mcp_integration_id,
                    workspace_id=entry_workspace_id,
                    organization_id=organization_id,
                    scope_name=scope_name,
                    artifact_type=artifact_type,
                    artifact_key=artifact_key,
                    artifact_ref=artifact_ref,
                    display_name=display_name,
                    description=description,
                )
            )

        return MCPCatalogAuthorizationResult(
            workspace_id=workspace_id,
            organization_id=organization_id,
            is_org_admin=is_org_admin,
            allowed_scope_names=frozenset(allowed_scope_names),
            entries=tuple(entries),
        )

    async def _validate_workspace(self, workspace_id: WorkspaceID) -> OrganizationID:
        stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
        result = await self.session.execute(stmt)
        organization_id = result.scalar_one_or_none()
        if organization_id is None:
            raise TracecatNotFoundError("Workspace not found")
        if organization_id != self.organization_id:
            raise TracecatAuthorizationError(
                "Workspace is outside the caller organization"
            )
        return organization_id

    async def _get_effective_scopes(self) -> frozenset[str]:
        if self.role.scopes:
            return frozenset(self.role.scopes)
        return frozenset(await compute_effective_scopes(self.role))
