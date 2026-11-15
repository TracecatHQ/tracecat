"""Register, change and remove the Tracecat Slack app."""

from __future__ import annotations

from sqlalchemy import func, select

from tracecat.admin.slack_app.schemas import (
    SlackPlatformAppRead,
    SlackPlatformAppUpdate,
)
from tracecat.agent.channel_bots.platform_app import (
    SlackPlatformApp,
    UnreadableSlackPlatformApp,
    delete_slack_platform_app,
    read_slack_platform_app,
    write_slack_platform_app,
)
from tracecat.agent.channel_bots.schemas import (
    ChannelBotAppOwner,
    ChannelBotPlatformAppSetup,
    ChannelBotProvider,
    ChannelBotStatus,
    ChannelBotTransport,
)
from tracecat.audit.logger import audit_log
from tracecat.db.models import AgentChannelBot
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.service import BasePlatformService


class SlackPlatformAppStillInstalled(TracecatConflictError):
    """Organizations still have the Tracecat Slack app installed."""


class SlackPlatformAppService(BasePlatformService):
    """Callers clear the platform app cache after a save or removal commits."""

    service_name = "slack_platform_app"

    async def get(self, setup: ChannelBotPlatformAppSetup) -> SlackPlatformAppRead:
        try:
            app = await read_slack_platform_app(self.session)
        except UnreadableSlackPlatformApp:
            return _read(None, setup, unreadable=True)
        return _read(app, setup, unreadable=False)

    @audit_log(resource_type="platform_setting", action="update")
    async def save(
        self, params: SlackPlatformAppUpdate, setup: ChannelBotPlatformAppSetup
    ) -> SlackPlatformAppRead:
        """Refuses a registration the deployment cannot serve."""
        try:
            stored = await read_slack_platform_app(self.session)
        except UnreadableSlackPlatformApp:
            stored = None
        # Another app's secrets never carry over.
        kept = stored if stored is not None and stored.app_id == params.app_id else None
        client_secret = params.client_secret or (kept.client_secret if kept else "")
        signing_secret = params.signing_secret or (kept.signing_secret if kept else "")
        # A refresh token belongs to the app-level token it was issued with.
        if params.app_token is not None:
            app_token, app_refresh_token = params.app_token, params.app_refresh_token
        elif kept is not None:
            app_token, app_refresh_token = kept.app_token, kept.app_refresh_token
        else:
            app_token = app_refresh_token = None
        missing = [
            name
            for name, value in (
                ("client secret", client_secret),
                ("signing secret", signing_secret),
            )
            if not value
        ]
        if setup.transport == ChannelBotTransport.SOCKET and app_token is None:
            missing.append("app-level token, which Socket Mode needs")
        if missing:
            raise TracecatValidationError(
                f"The Tracecat Slack app needs its {', '.join(missing)}"
            )
        app = SlackPlatformApp(
            app_id=params.app_id,
            client_id=params.client_id,
            client_secret=client_secret,
            signing_secret=signing_secret,
            app_token=app_token,
            app_refresh_token=app_refresh_token,
        )
        await write_slack_platform_app(self.session, app)
        await self.session.commit()
        return _read(app, setup, unreadable=False)

    @audit_log(resource_type="platform_setting", action="delete")
    async def remove(self) -> None:
        """Refused while any organization still has the app installed."""
        installs = await self.session.scalar(
            select(func.count())
            .select_from(AgentChannelBot)
            .where(
                AgentChannelBot.provider == ChannelBotProvider.SLACK,
                AgentChannelBot.app_owner == ChannelBotAppOwner.PLATFORM,
                AgentChannelBot.provider_tenant_id.is_not(None),
                AgentChannelBot.status != ChannelBotStatus.DISCONNECTED,
            )
        )
        if installs:
            raise SlackPlatformAppStillInstalled(
                f"{installs} Slack workspace(s) still have the Tracecat Slack app"
                " installed; disconnect them before removing the app"
            )
        await delete_slack_platform_app(self.session)
        await self.session.commit()


def _read(
    app: SlackPlatformApp | None,
    setup: ChannelBotPlatformAppSetup,
    *,
    unreadable: bool,
) -> SlackPlatformAppRead:
    return SlackPlatformAppRead(
        app_id=None if app is None else app.app_id,
        client_id=None if app is None else app.client_id,
        client_secret_set=app is not None,
        signing_secret_set=app is not None,
        app_token_set=app is not None and app.app_token is not None,
        unreadable=unreadable,
        setup=setup,
    )
