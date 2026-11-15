"""Register, change and remove the Tracecat Slack app."""

from __future__ import annotations

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
    ChannelBotPlatformAppSetup,
    ChannelBotTransport,
)
from tracecat.audit.logger import audit_log
from tracecat.exceptions import TracecatValidationError
from tracecat.service import BasePlatformService


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
        app_token = params.app_token or (kept.app_token if kept else None)
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
        )
        await write_slack_platform_app(self.session, app)
        await self.session.commit()
        return _read(app, setup, unreadable=False)

    @audit_log(resource_type="platform_setting", action="delete")
    async def remove(self) -> None:
        """Installs of the app stop serving until it is registered again."""
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
