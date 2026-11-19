from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.database import TEST_DB_CONFIG
from tracecat.admin.slack_app.schemas import (
    SlackPlatformAppSetupStepsUpdate,
    SlackPlatformAppUpdate,
)
from tracecat.admin.slack_app.service import SlackPlatformAppService
from tracecat.agent.channel_bots.platform_app import (
    SLACK_PLATFORM_APP_KEY,
    SlackPlatformApp,
    lock_slack_platform_app_for_refresh,
    read_slack_platform_app,
    store_slack_platform_app_tokens,
    write_slack_platform_app,
)
from tracecat.agent.channel_bots.schemas import (
    ChannelBotAppManifest,
    ChannelBotPlatformAppSetup,
    ChannelBotTransport,
)
from tracecat.audit.service import AuditService
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import PlatformRole
from tracecat.db.models import PlatformSetting
from tracecat.secrets.encryption import encrypt_value

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]

PAST = datetime(2026, 1, 1, tzinfo=UTC)
ROTATING = SlackPlatformApp(
    app_id="A0SYNTHETIC",
    client_id="111.222",
    client_secret="synthetic-client-secret",
    signing_secret="synthetic-signing-secret",
    app_token="xoxe.xapp-synthetic-rotating",
    app_refresh_token="xoxe-synthetic-refresh",
    app_token_refresh_due_at=PAST,
    icon_uploaded=True,
    distribution_enabled=True,
)
REFRESHED = ROTATING.model_copy(
    update={
        "app_token": "xoxe.xapp-synthetic-refreshed",
        "app_refresh_token": "xoxe-synthetic-refreshed",
        "app_token_refresh_due_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
)


async def store_refreshed(
    session: AsyncSession, refresh_due_at: datetime | None = None
) -> None:
    assert REFRESHED.app_token and REFRESHED.app_refresh_token
    await store_slack_platform_app_tokens(
        session,
        app_token=REFRESHED.app_token,
        app_refresh_token=REFRESHED.app_refresh_token,
        refresh_due_at=refresh_due_at or datetime(2026, 1, 2, tzinfo=UTC),
    )


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Independent committed transactions, for row locks one session cannot show."""
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                delete(PlatformSetting).where(
                    PlatformSetting.key == SLACK_PLATFORM_APP_KEY
                )
            )
            await cleanup.commit()
        await engine.dispose()


async def register(session: AsyncSession, app: SlackPlatformApp) -> None:
    await write_slack_platform_app(session, app)
    await session.commit()


async def test_a_registration_stored_before_the_refresh_time_reads_it_as_none(
    session: AsyncSession,
) -> None:
    old = ROTATING.model_dump(mode="json", exclude={"app_token_refresh_due_at"})
    session.add(
        PlatformSetting(
            key=SLACK_PLATFORM_APP_KEY,
            value=encrypt_value(json.dumps(old).encode(), key=get_db_encryption_key()),
            value_type="json",
            is_encrypted=True,
        )
    )
    await session.commit()

    stored = await read_slack_platform_app(session)

    assert stored == ROTATING.model_copy(update={"app_token_refresh_due_at": None})


@pytest.mark.parametrize(
    ("update", "locked"),
    [
        ({}, True),
        ({"app_token_refresh_due_at": datetime.now(UTC) + timedelta(hours=1)}, False),
        ({"app_token_refresh_due_at": None}, False),
        (
            {
                "app_token": "xapp-synthetic-app-token",
                "app_refresh_token": None,
                "app_token_refresh_due_at": None,
            },
            False,
        ),
        ({"app_refresh_token": None}, False),
        ({"app_token": None, "app_refresh_token": None}, False),
    ],
)
async def test_the_refresh_lock_returns_only_a_due_rotating_app(
    session: AsyncSession, update: dict[str, object], locked: bool
) -> None:
    await register(session, ROTATING.model_copy(update=update))

    app = await lock_slack_platform_app_for_refresh(session)

    assert app == (ROTATING.model_copy(update=update) if locked else None)


async def test_the_refresh_lock_returns_none_without_a_readable_registration(
    session: AsyncSession,
) -> None:
    assert await lock_slack_platform_app_for_refresh(session) is None
    session.add(
        PlatformSetting(
            key=SLACK_PLATFORM_APP_KEY,
            value=b"not-a-ciphertext",
            value_type="json",
            is_encrypted=True,
        )
    )
    await session.commit()

    assert await lock_slack_platform_app_for_refresh(session) is None


async def test_a_second_refresher_skips_the_app_another_holds(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as seed:
        await register(seed, ROTATING)

    async with sessions() as first, sessions() as second:
        assert await lock_slack_platform_app_for_refresh(first) == ROTATING
        assert await lock_slack_platform_app_for_refresh(second) is None
        await first.rollback()
        await second.rollback()
        assert await lock_slack_platform_app_for_refresh(second) == ROTATING


async def test_storing_refreshed_tokens_keeps_everything_else(
    session: AsyncSession,
) -> None:
    await register(session, ROTATING)
    assert await lock_slack_platform_app_for_refresh(session) is not None

    await store_refreshed(session)
    await session.commit()

    assert await read_slack_platform_app(session) == REFRESHED


async def test_storing_a_refresh_time_without_a_timezone_is_refused(
    session: AsyncSession,
) -> None:
    await register(session, ROTATING)

    with pytest.raises(ValidationError):
        await store_refreshed(session, refresh_due_at=datetime(2026, 1, 2))


@pytest.mark.parametrize("change", ["save", "mark_setup_steps"])
async def test_an_admin_change_waits_for_an_in_flight_refresh_and_keeps_its_tokens(
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    monkeypatch.setattr(AuditService, "create_event", AsyncMock())
    role = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    setup = ChannelBotPlatformAppSetup(
        transport=ChannelBotTransport.HTTP,
        oauth_redirect_url="https://api.example.com/oauth/callback",
        request_url="https://api.example.com/platform-events",
        manifest=ChannelBotAppManifest(
            filename="slack-app-manifest.json",
            media_type="application/json",
            encoding="text",
            content="{}",
            create_url=None,
        ),
    )
    async with sessions() as seed:
        await register(seed, ROTATING)

    async with sessions() as refreshing, sessions() as saving:
        assert await lock_slack_platform_app_for_refresh(refreshing) is not None
        save_pid = await saving.scalar(text("SELECT pg_backend_pid()"))
        service = SlackPlatformAppService(saving, role=role)

        async def admin_change() -> None:
            if change == "save":
                update = SlackPlatformAppUpdate(
                    app_id="A0SYNTHETIC", client_id="333.444"
                )
                await service.save(update, setup)
            else:
                steps = SlackPlatformAppSetupStepsUpdate(icon_uploaded=False)
                await service.mark_setup_steps(steps, setup)

        save = asyncio.create_task(admin_change())
        try:
            async with sessions() as observer:
                for _ in range(100):
                    wait_event = await observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": save_pid},
                    )
                    if wait_event == "Lock":
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("the admin save never waited on the refresh's row lock")
            await store_refreshed(refreshing)
            await refreshing.commit()
            await save
        finally:
            # Never outlive the sessions closing below.
            if not save.done():
                save.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await save

    async with sessions() as check:
        stored = await read_slack_platform_app(check)
    assert stored is not None
    changed = {"client_id": "333.444"} if change == "save" else {"icon_uploaded": False}
    assert stored == REFRESHED.model_copy(update=changed)
