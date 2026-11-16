"""Register, change and remove the Tracecat Slack app."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from tracecat.admin.slack_app.schemas import (
    SlackPlatformAppRead,
    SlackPlatformAppSetupStepsUpdate,
    SlackPlatformAppUpdate,
)
from tracecat.agent.channel_bots.platform_app import (
    SlackPlatformApp,
    UnreadableSlackPlatformApp,
    delete_slack_platform_app,
    is_live_platform_install,
    read_slack_platform_app,
    write_slack_platform_app,
)
from tracecat.agent.channel_bots.schemas import (
    ChannelBotPlatformAppSetup,
    ChannelBotProvider,
    ChannelBotTransport,
)
from tracecat.audit.logger import audit_log
from tracecat.db.models import AgentChannelBot
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.service import BasePlatformService


class SlackPlatformAppStillInstalled(TracecatConflictError):
    """Organizations still have the Tracecat Slack app installed."""


class SlackPlatformAppNotRegistered(TracecatNotFoundError):
    """No readable Tracecat Slack app is registered."""


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
            # Locked so an in-flight token refresh commits before its tokens are kept.
            stored = await read_slack_platform_app(self.session, for_update=True)
        except UnreadableSlackPlatformApp:
            stored = None
        # By the installs' own app ID, so an unreadable registration is guarded too.
        await self._require_no_installs(
            "replacing it with another Slack app", except_app_id=params.app_id
        )
        # Another app's secrets never carry over.
        kept = stored if stored is not None and stored.app_id == params.app_id else None
        client_secret = params.client_secret or (kept.client_secret if kept else "")
        signing_secret = params.signing_secret or (kept.signing_secret if kept else "")
        # A refresh token belongs to the app-level token it was issued with.
        if params.app_token is not None:
            app_token, app_refresh_token = params.app_token, params.app_refresh_token
            # A pasted token's expiry is unknown, so the next sweep refreshes it.
            refresh_due_at = datetime.now(UTC) if app_refresh_token else None
        elif kept is not None:
            app_token, app_refresh_token = kept.app_token, kept.app_refresh_token
            refresh_due_at = kept.app_token_refresh_due_at
        else:
            app_token = app_refresh_token = refresh_due_at = None
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
            app_token_refresh_due_at=refresh_due_at,
            icon_uploaded=kept.icon_uploaded if kept else False,
            distribution_enabled=kept.distribution_enabled if kept else False,
        )
        await write_slack_platform_app(self.session, app)
        await self.session.commit()
        return _read(app, setup, unreadable=False)

    @audit_log(resource_type="platform_setting", action="update")
    async def mark_setup_steps(
        self,
        params: SlackPlatformAppSetupStepsUpdate,
        setup: ChannelBotPlatformAppSetup,
    ) -> SlackPlatformAppRead:
        try:
            stored = await read_slack_platform_app(self.session, for_update=True)
        except UnreadableSlackPlatformApp:
            stored = None
        if stored is None:
            raise SlackPlatformAppNotRegistered(
                "Register the Tracecat Slack app before marking its setup steps"
            )
        app = stored.model_copy(update=params.model_dump(exclude_none=True))
        await write_slack_platform_app(self.session, app)
        await self.session.commit()
        return _read(app, setup, unreadable=False)

    @audit_log(resource_type="platform_setting", action="delete")
    async def remove(self) -> None:
        """Refused while any organization still has the app installed."""
        await self._require_no_installs("removing the app")
        await delete_slack_platform_app(self.session)
        await self.session.commit()

    async def _require_no_installs(
        self, action: str, *, except_app_id: str | None = None
    ) -> None:
        statement = (
            select(func.count())
            .select_from(AgentChannelBot)
            .where(
                AgentChannelBot.provider == ChannelBotProvider.SLACK,
                is_live_platform_install(),
            )
        )
        if except_app_id is not None:
            statement = statement.where(
                AgentChannelBot.provider_app_id.is_distinct_from(except_app_id)
            )
        installs = await self.session.scalar(statement)
        if installs:
            raise SlackPlatformAppStillInstalled(
                f"{installs} Slack workspace(s) still have the Tracecat Slack app"
                f" installed; disconnect them before {action}"
            )


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
        icon_uploaded=app is not None and app.icon_uploaded,
        distribution_enabled=app is not None and app.distribution_enabled,
        unreadable=unreadable,
        setup=setup,
    )
