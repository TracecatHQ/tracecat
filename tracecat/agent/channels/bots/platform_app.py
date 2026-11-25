"""The Tracecat Slack app: one Slack app a platform admin registers, which every
organization installs on its own Slack tenant.

Stored as one encrypted platform setting, so a registration is never partial.
"""

from __future__ import annotations

from datetime import UTC, datetime

from async_lru import alru_cache
from cryptography.fernet import InvalidToken
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channels.bots.schemas import ChannelBotAppOwner, ChannelBotStatus
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import AgentChannelBot, PlatformSetting
from tracecat.logger import logger
from tracecat.secrets.encryption import decrypt_value, encrypt_value

SLACK_PLATFORM_APP_KEY = "agent_channel_slack_platform_app"
# A save clears this process's copy; other API processes see it within this long.
SLACK_PLATFORM_APP_CACHE_SECONDS = 30


class SlackPlatformApp(BaseModel):
    """The registered app's identity and secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    app_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1, repr=False)
    signing_secret: str = Field(min_length=1, repr=False)
    # Socket Mode only: the app-level `xapp-` token with `connections:write`.
    app_token: str | None = Field(default=None, repr=False)
    # Only beside a rotating `xoxe.xapp-` app-level token.
    app_refresh_token: str | None = Field(default=None, repr=False)
    # When the rotating app-level token is next refreshed; None when it doesn't rotate.
    app_token_refresh_due_at: AwareDatetime | None = None
    # Slack-only settings an admin marks done; a different app ID starts them over.
    icon_uploaded: bool = False
    distribution_enabled: bool = False


def is_live_platform_install() -> ColumnElement[bool]:
    """A platform app install that holds a provider tenant and is not disconnected."""
    return and_(
        AgentChannelBot.app_owner == ChannelBotAppOwner.PLATFORM,
        AgentChannelBot.provider_tenant_id.is_not(None),
        AgentChannelBot.status != ChannelBotStatus.DISCONNECTED,
    )


class UnreadableSlackPlatformApp(Exception):
    """The stored registration no longer decrypts; a platform admin saves it again."""


class UnregisteredSlackPlatformApp(Exception):
    """No Tracecat Slack app is registered."""


async def read_slack_platform_app(
    session: AsyncSession, *, for_update: bool = False
) -> SlackPlatformApp | None:
    """The registered app, or None. Raises `UnreadableSlackPlatformApp`."""
    statement = select(PlatformSetting.value).where(
        PlatformSetting.key == SLACK_PLATFORM_APP_KEY
    )
    if for_update:
        statement = statement.with_for_update()
    raw = await session.scalar(statement)
    if raw is None:
        return None
    return _decrypt(raw)


def _decrypt(raw: bytes) -> SlackPlatformApp:
    # Outside the try: a missing key is a deployment error, not an unreadable row.
    key = get_db_encryption_key()
    try:
        plain = decrypt_value(raw, key=key)
        return SlackPlatformApp.model_validate_json(plain)
    except (InvalidToken, ValueError) as e:
        raise UnreadableSlackPlatformApp from e


async def lock_slack_platform_app_for_refresh(
    session: AsyncSession,
) -> SlackPlatformApp | None:
    """The app, row-locked until the caller's transaction ends, if its rotating token is due."""
    raw = await session.scalar(
        select(PlatformSetting.value)
        .where(PlatformSetting.key == SLACK_PLATFORM_APP_KEY)
        .with_for_update(skip_locked=True)
    )
    if raw is None:
        return None
    try:
        app = _decrypt(raw)
    except UnreadableSlackPlatformApp:
        logger.error(
            "The Tracecat Slack app's registration does not decrypt; its app-level"
            " token cannot be refreshed until a platform admin saves it again"
        )
        return None
    due = app.app_token_refresh_due_at
    if (
        app.app_token is None
        or app.app_refresh_token is None
        or due is None
        or due > datetime.now(UTC)
    ):
        return None
    return app


async def store_slack_platform_app_tokens(
    session: AsyncSession,
    *,
    app_token: str,
    app_refresh_token: str,
    refresh_due_at: datetime,
) -> SlackPlatformApp:
    """Replace only the rotated tokens and their due time; the caller commits.

    Raises `UnregisteredSlackPlatformApp` or `UnreadableSlackPlatformApp`.
    """
    stored = await read_slack_platform_app(session, for_update=True)
    if stored is None:
        raise UnregisteredSlackPlatformApp
    # Validated, so a naive due time never reaches storage and fails every later read.
    app = SlackPlatformApp.model_validate(
        stored.model_dump()
        | {
            "app_token": app_token,
            "app_refresh_token": app_refresh_token,
            "app_token_refresh_due_at": refresh_due_at,
        }
    )
    await write_slack_platform_app(session, app)
    return app


async def write_slack_platform_app(
    session: AsyncSession, app: SlackPlatformApp
) -> None:
    """Replace the registration; the caller commits."""
    value = encrypt_value(app.model_dump_json().encode(), key=get_db_encryption_key())
    await session.execute(
        insert(PlatformSetting)
        .values(
            key=SLACK_PLATFORM_APP_KEY,
            value=value,
            value_type="json",
            is_encrypted=True,
        )
        .on_conflict_do_update(
            index_elements=[PlatformSetting.key],
            set_={"value": value, "is_encrypted": True, "updated_at": func.now()},
        )
    )


async def delete_slack_platform_app(session: AsyncSession) -> None:
    """Drop the registration; the caller commits."""
    await session.execute(
        delete(PlatformSetting).where(PlatformSetting.key == SLACK_PLATFORM_APP_KEY)
    )


@alru_cache(ttl=SLACK_PLATFORM_APP_CACHE_SECONDS)
async def slack_platform_app() -> SlackPlatformApp | None:
    """The registered app as requests use it; an unreadable one serves nothing."""
    async with get_async_session_bypass_rls_context_manager() as session:
        try:
            return await read_slack_platform_app(session)
        except UnreadableSlackPlatformApp:
            logger.error(
                "The Tracecat Slack app's registration does not decrypt; a platform"
                " admin must save it again"
            )
            return None


def clear_slack_platform_app_cache() -> None:
    """Make a committed save or removal visible to this process's next request."""
    slack_platform_app.cache_clear()
