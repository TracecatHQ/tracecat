"""Persisted MCP catalog search service for external wrapper operations."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Float, Text, case, cast, func, literal, or_, select

from tracecat.db.models import MCPIntegration, MCPIntegrationCatalogEntry
from tracecat.identifiers import WorkspaceID
from tracecat.integrations.enums import MCPCatalogArtifactType
from tracecat.integrations.mcp_scopes import build_mcp_scope_name
from tracecat.mcp.catalog.types import MCPCatalogSearchResult, MCPCatalogSearchResults
from tracecat.mcp.policy.service import MCPCatalogPolicyService
from tracecat.service import BaseOrgService

_LIKE_ESCAPE_CHAR = "\\"


def _escape_like_pattern(value: str) -> str:
    return (
        value.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{_LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{_LIKE_ESCAPE_CHAR}_")
    )


class MCPCatalogSearchService(BaseOrgService):
    """Search active persisted catalog entries visible to the current caller."""

    service_name = "mcp_catalog_search"

    async def search_catalog(
        self,
        *,
        workspace_id: WorkspaceID,
        query: str,
        artifact_types: Sequence[MCPCatalogArtifactType] | None = None,
        integration_ids: Sequence[UUID] | None = None,
        limit: int = 20,
    ) -> MCPCatalogSearchResults:
        """Search policy-authorized persisted catalog entries for a workspace."""
        policy_service = MCPCatalogPolicyService(session=self.session, role=self.role)
        authorization = await policy_service.authorize_catalog_search(
            workspace_id=workspace_id
        )
        if not authorization.allowed_entry_ids:
            return MCPCatalogSearchResults(
                workspace_id=workspace_id,
                query=query,
                results=(),
            )

        normalized_query = query.strip().lower()
        normalized_query_text = cast(literal(normalized_query), Text)
        display_name = func.lower(
            func.coalesce(MCPIntegrationCatalogEntry.display_name, "")
        )
        artifact_ref = func.lower(MCPIntegrationCatalogEntry.artifact_ref)
        base_stmt = (
            select(
                MCPIntegrationCatalogEntry.id,
                MCPIntegrationCatalogEntry.mcp_integration_id,
                MCPIntegrationCatalogEntry.workspace_id,
                MCPIntegrationCatalogEntry.artifact_type,
                MCPIntegrationCatalogEntry.artifact_key,
                MCPIntegrationCatalogEntry.artifact_ref,
                MCPIntegrationCatalogEntry.display_name,
                MCPIntegrationCatalogEntry.description,
                MCPIntegrationCatalogEntry.input_schema,
                MCPIntegration.scope_namespace,
            )
            .join(
                MCPIntegration,
                MCPIntegration.id == MCPIntegrationCatalogEntry.mcp_integration_id,
            )
            .where(
                MCPIntegrationCatalogEntry.workspace_id == workspace_id,
                MCPIntegrationCatalogEntry.is_active.is_(True),
                MCPIntegrationCatalogEntry.id.in_(authorization.allowed_entry_ids),
            )
        )
        if artifact_types:
            base_stmt = base_stmt.where(
                MCPIntegrationCatalogEntry.artifact_type.in_(
                    [artifact_type.value for artifact_type in artifact_types]
                )
            )
        if integration_ids:
            base_stmt = base_stmt.where(
                MCPIntegrationCatalogEntry.mcp_integration_id.in_(integration_ids)
            )

        if normalized_query:
            tsquery = func.websearch_to_tsquery("simple", query)
            prefix_pattern = f"{_escape_like_pattern(normalized_query)}%"
            prefix_match = display_name.like(prefix_pattern, escape=_LIKE_ESCAPE_CHAR)
            similarity_score = func.greatest(
                func.similarity(display_name, normalized_query_text),
                func.similarity(artifact_ref, normalized_query_text),
            )
            rank = (
                cast(
                    func.ts_rank_cd(
                        MCPIntegrationCatalogEntry.search_vector, tsquery, 32
                    ),
                    Float,
                )
                + cast(
                    case((display_name == normalized_query, 1.25), else_=0.0),
                    Float,
                )
                + cast(
                    case((artifact_ref == normalized_query, 1.0), else_=0.0),
                    Float,
                )
                + cast(
                    case((prefix_match, 0.35), else_=0.0),
                    Float,
                )
                + cast(similarity_score * 0.20, Float)
            ).label("rank")
            stmt = (
                base_stmt.add_columns(rank)
                .where(
                    or_(
                        MCPIntegrationCatalogEntry.search_vector.op("@@")(tsquery),
                        display_name == normalized_query,
                        artifact_ref == normalized_query,
                        prefix_match,
                        func.similarity(display_name, normalized_query_text) >= 0.1,
                        func.similarity(artifact_ref, normalized_query_text) >= 0.1,
                    )
                )
                .order_by(
                    rank.desc(),
                    MCPIntegrationCatalogEntry.artifact_type,
                    display_name,
                    MCPIntegrationCatalogEntry.artifact_ref,
                    MCPIntegrationCatalogEntry.id,
                )
            )
        else:
            rank = literal(0.0, type_=Float).label("rank")
            stmt = base_stmt.add_columns(rank).order_by(
                MCPIntegrationCatalogEntry.artifact_type,
                display_name,
                MCPIntegrationCatalogEntry.artifact_ref,
                MCPIntegrationCatalogEntry.id,
            )

        result = await self.session.execute(stmt.limit(limit))
        rows = result.tuples().all()
        items: list[MCPCatalogSearchResult] = []
        for (
            entry_id,
            mcp_integration_id,
            entry_workspace_id,
            artifact_type_value,
            artifact_key,
            artifact_ref_value,
            display_name_value,
            description,
            input_schema,
            scope_namespace,
            rank_value,
        ) in rows:
            artifact_type = MCPCatalogArtifactType(artifact_type_value)
            scope_name, _resource, _action = build_mcp_scope_name(
                scope_namespace=scope_namespace,
                artifact_type=artifact_type,
                artifact_key=artifact_key,
            )
            items.append(
                MCPCatalogSearchResult(
                    id=entry_id,
                    mcp_integration_id=mcp_integration_id,
                    workspace_id=entry_workspace_id,
                    artifact_type=artifact_type,
                    artifact_key=artifact_key,
                    artifact_ref=artifact_ref_value,
                    display_name=display_name_value,
                    description=description,
                    input_schema=input_schema,
                    scope_name=scope_name,
                    rank=float(rank_value),
                )
            )

        return MCPCatalogSearchResults(
            workspace_id=workspace_id,
            query=query,
            results=tuple(items),
        )
