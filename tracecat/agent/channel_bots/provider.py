"""The channel bot admin, found among the installed API extensions."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots.schemas import (
    AgentChannelBotCreate,
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    ChannelBotInstall,
    ChannelBotInstallResult,
    ChannelBotOAuthCallback,
    ChannelBotOAuthResult,
    ChannelBotProvider,
)
from tracecat.api.extensions import load_api_extensions
from tracecat.auth.types import Role
from tracecat.pagination import CursorPaginatedResponse, PageParams


class ChannelBotAdmin(Protocol):
    """Workspace-scoped by `role` except `complete_oauth`. Raise `TracecatNotFoundError`
    for an unknown bot or preset, `TracecatValidationError` for a refused request.
    """

    async def list_bots(
        self,
        session: AsyncSession,
        role: Role,
        *,
        agent_preset_id: uuid.UUID | None,
        provider: ChannelBotProvider | None,
        page: PageParams,
    ) -> CursorPaginatedResponse[AgentChannelBotRead]:
        """Return a page with ``has_more`` and ``has_previous`` set from its cursors.

        Raise ``PaginationError`` for a malformed or foreign cursor.
        """
        ...

    async def create_bot(
        self, session: AsyncSession, role: Role, params: AgentChannelBotCreate
    ) -> AgentChannelBotRead: ...

    async def update_bot(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        params: AgentChannelBotUpdate,
    ) -> AgentChannelBotRead: ...

    async def rotate_endpoint(
        self, session: AsyncSession, role: Role, bot_id: uuid.UUID
    ) -> AgentChannelBotRead: ...

    async def disconnect_bot(
        self, session: AsyncSession, role: Role, bot_id: uuid.UUID
    ) -> AgentChannelBotRead: ...

    async def install(
        self, session: AsyncSession, role: Role, params: ChannelBotInstall
    ) -> ChannelBotInstallResult: ...

    async def complete_oauth(
        self, provider: ChannelBotProvider, callback: ChannelBotOAuthCallback
    ) -> ChannelBotOAuthResult:
        """Public: the state carries the workspace and must be issued for `provider`.

        Return a failed result for a bad request instead of raising.
        """
        ...


@runtime_checkable
class ChannelBotAdminExtension(Protocol):
    """An API extension that also implements the channel bot admin routes."""

    def channel_bot_admin(self) -> ChannelBotAdmin: ...


@lru_cache(maxsize=1)
def get_channel_bot_admin() -> ChannelBotAdmin | None:
    """The one installed admin, or None without one."""
    admins = {
        name: extension.channel_bot_admin()
        for name, extension in load_api_extensions().items()
        if isinstance(extension, ChannelBotAdminExtension)
    }
    if len(admins) > 1:
        names = ", ".join(sorted(admins))
        raise RuntimeError(f"Multiple channel bot admins installed: {names}")
    return next(iter(admins.values()), None)
