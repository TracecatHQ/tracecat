"""The channel bot admin, found among the installed API extensions."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Protocol, runtime_checkable

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots.schemas import (
    AgentChannelBotCreate,
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    AgentChannelRouteRead,
    AgentChannelRouteSet,
    ChannelBotAppOwner,
    ChannelBotChannel,
    ChannelBotInstall,
    ChannelBotInstallResult,
    ChannelBotOAuthCallback,
    ChannelBotOAuthResult,
    ChannelBotPlatformApp,
    ChannelBotPlatformAppSetup,
    ChannelBotPlatformInstall,
    ChannelBotProvider,
)
from tracecat.api.extensions import load_api_extensions
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatException
from tracecat.pagination import CursorPaginatedResponse, PageParams


class ChannelListingNotSupported(TracecatException):
    """The bot's provider cannot list its channels; the API answers 501."""


class ChannelListingNeedsReinstall(TracecatException):
    """The bot's app lacks the scopes to list channels; the API answers 424."""


class ChannelBotAdmin(Protocol):
    """Workspace-scoped by `role` except `complete_oauth` and the deployment-wide
    `platform_app_setup` and `platform_app_manifest`. Raise `TracecatNotFoundError` for an unknown bot or preset,
    `TracecatValidationError` for a refused request.
    """

    async def list_bots(
        self,
        session: AsyncSession,
        role: Role,
        *,
        agent_preset_id: uuid.UUID | None,
        provider: ChannelBotProvider | None,
        app_owner: ChannelBotAppOwner | None,
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

    async def list_platform_apps(
        self, session: AsyncSession, role: Role
    ) -> list[ChannelBotPlatformApp]:
        """The deployment's own provider apps; empty when it registers none."""
        ...

    def platform_app_setup(
        self, provider: ChannelBotProvider
    ) -> ChannelBotPlatformAppSetup:
        """How the deployment's own app for `provider` reaches it."""
        ...

    def platform_app_manifest(
        self, provider: ChannelBotProvider
    ) -> dict[str, JsonValue]:
        """The app manifest that creates the deployment's own app in `provider`."""
        ...

    async def app_manifest(
        self, session: AsyncSession, role: Role, bot_id: uuid.UUID, *, app_name: str
    ) -> dict[str, JsonValue]:
        """The app manifest that creates the bot's own app, named `app_name`.

        Raise `TracecatValidationError` for a platform install, which has none.
        """
        ...

    async def install_platform(
        self, session: AsyncSession, role: Role, params: ChannelBotPlatformInstall
    ) -> ChannelBotInstallResult: ...

    async def list_routes(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        *,
        agent_preset_id: uuid.UUID | None,
        page: PageParams,
    ) -> CursorPaginatedResponse[AgentChannelRouteRead]:
        """A page of the install's routes, only those to `agent_preset_id` when given,
        ordered by `provider_channel_id` ascending.

        Raise ``PaginationError`` for a malformed or foreign cursor.
        """
        ...

    async def add_route(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        params: AgentChannelRouteSet,
    ) -> AgentChannelRouteRead:
        """Raise `TracecatConflictError` when the channel already has a route."""
        ...

    async def list_channels(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        *,
        page: PageParams,
    ) -> CursorPaginatedResponse[ChannelBotChannel]:
        """A page of the channels the bot is a member of, for an install of any app,
        paged by the provider's own cursor.

        Raise `TracecatValidationError` unless the bot is installed and running,
        `ChannelListingNotSupported` when its provider cannot list channels,
        `ChannelListingNeedsReinstall` when its app lacks the scopes to, and
        ``PaginationError`` for a malformed cursor.
        """
        ...

    async def list_routable_channels(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        *,
        page: PageParams,
    ) -> CursorPaginatedResponse[ChannelBotChannel]:
        """A page of every channel in a platform install's provider tenant, which a
        route can name, paged by the provider's own cursor.

        Raise `TracecatValidationError` unless the bot is a platform install, installed
        and running, `ChannelListingNotSupported` when its provider cannot list
        channels, `ChannelListingNeedsReinstall` when its app lacks the scopes to, and
        ``PaginationError`` for a malformed cursor.
        """
        ...

    async def set_route(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        params: AgentChannelRouteSet,
    ) -> AgentChannelRouteRead: ...

    async def delete_route(
        self,
        session: AsyncSession,
        role: Role,
        bot_id: uuid.UUID,
        provider_channel_id: str,
    ) -> None: ...

    def thread_channel_id(
        self, provider: ChannelBotProvider, provider_thread_id: dict[str, JsonValue]
    ) -> str | None:
        """The channel a linked thread is in, as `list_channels` names it. No I/O.

        Return None for a thread `provider` cannot read, instead of raising.
        """
        ...

    async def complete_oauth(
        self,
        provider: ChannelBotProvider,
        callback: ChannelBotOAuthCallback,
        *,
        user_id: uuid.UUID | None,
    ) -> ChannelBotOAuthResult:
        """Public: the state carries the workspace and must be issued for `provider`.
        `user_id` is the browser's signed-in user, if any.

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
