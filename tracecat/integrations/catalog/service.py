"""Runtime service for the platform MCP catalog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

import sqlalchemy as sa

from tracecat.db.models import MCPIntegration, OAuthIntegration
from tracecat.integrations.catalog.loader import get_platform_mcp_catalog_entries
from tracecat.integrations.catalog.types import PlatformMCPCatalogEntry
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.schemas import (
    PlatformMCPCatalogRead,
    PlatformMCPCatalogState,
    PlatformMCPCatalogStatus,
)
from tracecat.integrations.types import MCPServerType
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.secrets.encryption import is_set
from tracecat.service import BaseService

_CATALOG_STATUSES: frozenset[PlatformMCPCatalogStatus] = frozenset(
    {"available", "coming_soon", "deprecated", "hidden"}
)


class PlatformMCPCatalogService(BaseService):
    """Project the runtime platform MCP catalog with workspace state."""

    service_name = "platform_mcp_catalog"

    async def list_catalog(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_addons_entitled: bool,
        q: str | None = None,
        category: str | None = None,
        status: PlatformMCPCatalogStatus | None = None,
        cursor_params: CursorPaginationParams | None = None,
    ) -> tuple[list[PlatformMCPCatalogRead], str | None]:
        """List runtime catalog entries joined with workspace MCP state."""
        params = cursor_params or CursorPaginationParams(limit=50)
        entries = get_platform_mcp_catalog_entries(
            include_private=agent_addons_entitled
        )
        state_entries_by_id = {
            entry["id"]: entry
            for entry in get_platform_mcp_catalog_entries(include_private=True)
        }
        entries = self._filter_entries(
            entries,
            q=q,
            category=category,
            status=status,
        )
        entries.sort(key=lambda entry: (entry["sort_key"], str(entry["id"])))

        if params.cursor:
            cursor = BaseCursorPaginator.decode_cursor(params.cursor)
            if not isinstance(cursor.sort_value, str):
                raise ValueError("Invalid cursor")
            cursor_id = uuid.UUID(cursor.id)
            entries = [
                entry
                for entry in entries
                if (entry["sort_key"], entry["id"]) > (cursor.sort_value, cursor_id)
            ]

        page_entries = entries[: params.limit + 1]
        has_more = len(page_entries) > params.limit
        if has_more:
            page_entries = page_entries[: params.limit]

        state_entries = [
            state_entries_by_id.get(entry["id"], entry) for entry in page_entries
        ]
        state_by_catalog_id = await self._workspace_state_by_catalog_id(
            workspace_id=workspace_id,
            catalog_entries=state_entries,
        )
        now = datetime.now(UTC)
        items = [
            self._catalog_read_from_entry(
                entry=entry,
                state=state_by_catalog_id.get(entry["id"]),
                agent_addons_entitled=agent_addons_entitled,
                now=now,
            )
            for entry in page_entries
        ]

        next_cursor = None
        if has_more and page_entries:
            last_entry = page_entries[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                last_entry["id"],
                sort_column="sort_key",
                sort_value=last_entry["sort_key"],
            )
        return items, next_cursor

    @staticmethod
    def _filter_entries(
        entries: list[PlatformMCPCatalogEntry],
        *,
        q: str | None,
        category: str | None,
        status: PlatformMCPCatalogStatus | None,
    ) -> list[PlatformMCPCatalogEntry]:
        filtered = entries
        if q:
            needle = q.strip().casefold()
            if needle:
                filtered = [
                    entry
                    for entry in filtered
                    if needle in entry["name"].casefold()
                    or needle in entry["description"].casefold()
                    or needle in entry["slug"].casefold()
                ]
        if category:
            filtered = [entry for entry in filtered if entry["category"] == category]
        if status:
            filtered = [entry for entry in filtered if entry["status"] == status]
        return filtered

    async def _workspace_state_by_catalog_id(
        self,
        *,
        workspace_id: uuid.UUID,
        catalog_entries: list[PlatformMCPCatalogEntry],
    ) -> dict[uuid.UUID, tuple[MCPIntegration, bytes | None]]:
        if not catalog_entries:
            return {}

        provider_to_catalog_id = {
            provider_id: entry["id"]
            for entry in catalog_entries
            if isinstance(provider_id := entry.get("provider_id"), str) and provider_id
        }
        slug_to_catalog_id = {entry["slug"]: entry["id"] for entry in catalog_entries}
        rows = (
            await self.session.execute(
                sa.select(
                    MCPIntegration,
                    OAuthIntegration.encrypted_access_token.label(
                        "encrypted_access_token"
                    ),
                    OAuthIntegration.provider_id.label("provider_id"),
                )
                .outerjoin(
                    OAuthIntegration,
                    OAuthIntegration.id == MCPIntegration.oauth_integration_id,
                )
                .where(MCPIntegration.workspace_id == workspace_id)
            )
        ).all()

        state_by_catalog_id: dict[uuid.UUID, tuple[MCPIntegration, bytes | None]] = {}
        for mcp_integration, encrypted_access_token, provider_id in rows:
            if catalog_id := slug_to_catalog_id.get(mcp_integration.slug):
                state_by_catalog_id.setdefault(
                    catalog_id,
                    (mcp_integration, encrypted_access_token),
                )
                continue

            if (
                isinstance(provider_id, str)
                and (provider_catalog_id := provider_to_catalog_id.get(provider_id))
                and self._mcp_integration_has_provider_slug(
                    mcp_integration=mcp_integration,
                    provider_id=provider_id,
                )
            ):
                state_by_catalog_id.setdefault(
                    provider_catalog_id,
                    (mcp_integration, encrypted_access_token),
                )
        return state_by_catalog_id

    @staticmethod
    def _mcp_integration_has_provider_slug(
        *, mcp_integration: MCPIntegration, provider_id: str
    ) -> bool:
        if mcp_integration.slug == provider_id:
            return True
        suffix = mcp_integration.slug.removeprefix(f"{provider_id}-")
        return suffix != mcp_integration.slug and suffix.isdigit()

    @staticmethod
    def _catalog_state(
        *,
        mcp_integration: MCPIntegration | None,
        encrypted_access_token: bytes | None,
    ) -> PlatformMCPCatalogState:
        if mcp_integration is None:
            return "not_configured"
        if (
            mcp_integration.auth_type == MCPAuthType.OAUTH2
            and encrypted_access_token is not None
            and is_set(encrypted_access_token)
        ):
            return "connected"
        if mcp_integration.auth_type != MCPAuthType.OAUTH2:
            return "connected"
        return "configured"

    @staticmethod
    def _catalog_status(status: str) -> PlatformMCPCatalogStatus:
        if status in _CATALOG_STATUSES:
            return status
        return "coming_soon"

    @classmethod
    def _catalog_read_from_entry(
        cls,
        *,
        entry: PlatformMCPCatalogEntry,
        state: tuple[MCPIntegration, bytes | None] | None,
        agent_addons_entitled: bool,
        now: datetime,
    ) -> PlatformMCPCatalogRead:
        mcp_integration = state[0] if state else None
        encrypted_access_token = state[1] if state else None
        locked = not agent_addons_entitled and mcp_integration is None
        connection_spec = (
            entry.get("connection_spec") if agent_addons_entitled else None
        )
        connection_options = (
            (entry.get("connection_options") or []) if agent_addons_entitled else []
        )
        return PlatformMCPCatalogRead(
            id=entry["id"],
            slug=entry["slug"],
            name=entry["name"],
            description=entry["description"],
            category=entry["category"],
            status=cls._catalog_status(entry["status"]),
            icon_url=entry.get("icon_url"),
            docs_url=entry.get("docs_url") if agent_addons_entitled else None,
            provider_id=entry.get("provider_id") if agent_addons_entitled else None,
            connection_spec=connection_spec,
            connection_options=connection_options,
            locked=locked,
            state=cls._catalog_state(
                mcp_integration=mcp_integration,
                encrypted_access_token=encrypted_access_token,
            ),
            mcp_integration_id=mcp_integration.id if mcp_integration else None,
            mcp_server_type=cast(MCPServerType, mcp_integration.server_type)
            if mcp_integration and mcp_integration.server_type in {"http", "stdio"}
            else None,
            mcp_auth_type=mcp_integration.auth_type if mcp_integration else None,
            created_at=mcp_integration.created_at if mcp_integration else now,
            updated_at=mcp_integration.updated_at if mcp_integration else now,
            last_refreshed_at=None,
        )
