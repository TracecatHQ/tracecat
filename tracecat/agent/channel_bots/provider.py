"""The channel bot admin provider, found among the installed API extensions."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots.schemas import (
    AgentChannelBotCreate,
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    ChannelBotProvider,
    SlackBotOAuthCallback,
    SlackBotOAuthOutcome,
    SlackBotOAuthStartRequest,
    SlackBotOAuthStartResponse,
)
from tracecat.api.extensions import load_api_extensions
from tracecat.auth.types import Role
from tracecat.pagination import CursorPaginatedResponse, PageParams


class ChannelBotAdmin(Protocol):
    """Implements the admin routes. Every method but the OAuth callback is
    workspace-scoped by `role`; raise `TracecatNotFoundError` for an unknown bot
    or preset and `TracecatValidationError` for a request the bot's state refuses.
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
        """Raise ``PaginationError`` for a cursor from another query."""
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

    async def start_slack_oauth(
        self, session: AsyncSession, role: Role, params: SlackBotOAuthStartRequest
    ) -> SlackBotOAuthStartResponse: ...

    async def complete_slack_oauth(
        self, callback: SlackBotOAuthCallback
    ) -> SlackBotOAuthOutcome:
        """Public: the state carries the workspace. Never raises for a bad request."""
        ...


@runtime_checkable
class ChannelBotAdminExtension(Protocol):
    """An API extension that also implements the channel bot admin routes."""

    def channel_bot_admin(self) -> ChannelBotAdmin: ...


@lru_cache(maxsize=1)
def get_channel_bot_admin() -> ChannelBotAdmin | None:
    """The one installed provider, or None without one."""
    providers = {
        name: extension.channel_bot_admin()
        for name, extension in load_api_extensions().items()
        if isinstance(extension, ChannelBotAdminExtension)
    }
    if len(providers) > 1:
        names = ", ".join(sorted(providers))
        raise RuntimeError(f"Multiple channel bot admin providers installed: {names}")
    return next(iter(providers.values()), None)
