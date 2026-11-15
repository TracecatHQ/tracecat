"""The Tracecat Slack app: one Slack app a platform admin registers, which every
organization installs on its own Slack tenant.

Stored as one encrypted platform setting, so a registration is never partial.
"""

from __future__ import annotations

from async_lru import alru_cache
from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import PlatformSetting
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
    # Slack-only settings an admin marks done; a different app ID starts them over.
    icon_uploaded: bool = False
    distribution_enabled: bool = False


class UnreadableSlackPlatformApp(Exception):
    """The stored registration no longer decrypts; a platform admin saves it again."""


async def read_slack_platform_app(session: AsyncSession) -> SlackPlatformApp | None:
    """The registered app, or None. Raises `UnreadableSlackPlatformApp`."""
    raw = await session.scalar(
        select(PlatformSetting.value).where(
            PlatformSetting.key == SLACK_PLATFORM_APP_KEY
        )
    )
    if raw is None:
        return None
    try:
        plain = decrypt_value(raw, key=get_db_encryption_key())
        return SlackPlatformApp.model_validate_json(plain)
    except (InvalidToken, ValueError) as e:
        raise UnreadableSlackPlatformApp from e


async def write_slack_platform_app(
    session: AsyncSession, app: SlackPlatformApp
) -> None:
    """Replace the registration; the caller commits."""
    value = encrypt_value(app.model_dump_json().encode(), key=get_db_encryption_key())
    setting = await session.scalar(
        select(PlatformSetting).where(PlatformSetting.key == SLACK_PLATFORM_APP_KEY)
    )
    if setting is None:
        session.add(
            PlatformSetting(
                key=SLACK_PLATFORM_APP_KEY,
                value=value,
                value_type="json",
                is_encrypted=True,
            )
        )
    else:
        setting.value = value
        setting.is_encrypted = True


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
