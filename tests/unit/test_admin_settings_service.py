from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tracecat_ee.admin.settings import service as admin_settings_service_module
from tracecat_ee.admin.settings.schemas import PlatformAuditSettingsUpdate
from tracecat_ee.admin.settings.service import (
    AUDIT_SETTINGS_KEYS,
    AdminSettingsService,
)

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.audit.service import AuditService
from tracecat.auth.types import PlatformRole
from tracecat.db.models import PlatformSetting

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture(autouse=True)
def disable_audit_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AuditService, "create_event", AsyncMock())


@pytest.mark.anyio
async def test_platform_audit_settings_default_to_disconnected(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)

    settings = await service.get_audit_settings()

    assert settings.audit_webhook_url is None
    assert settings.audit_webhook_custom_headers is None
    assert settings.audit_webhook_custom_payload is None
    assert settings.audit_webhook_payload_attribute is None
    assert settings.audit_webhook_verify_ssl is True
    assert settings.decryption_failed_keys == []


@pytest.mark.anyio
async def test_platform_audit_settings_encrypt_sensitive_values(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AdminSettingsService(session, platform_role)
    clear_audit_cache = MagicMock()
    monkeypatch.setattr(
        admin_settings_service_module,
        "clear_audit_setting_cache",
        clear_audit_cache,
    )
    custom_headers = {"Authorization": "Bearer secret"}
    custom_payload = {"source": "tracecat-platform"}

    settings = await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://example.com/platform-audit",
            audit_webhook_custom_headers=custom_headers,
            audit_webhook_custom_payload=custom_payload,
            audit_webhook_payload_attribute="event",
            audit_webhook_verify_ssl=False,
        )
    )

    assert settings.audit_webhook_url == "https://example.com/platform-audit"
    assert settings.audit_webhook_custom_headers == custom_headers
    assert settings.audit_webhook_custom_payload == custom_payload
    assert settings.audit_webhook_payload_attribute == "event"
    assert settings.audit_webhook_verify_ssl is False
    clear_audit_cache.assert_called_once_with()

    rows = (await session.execute(select(PlatformSetting))).scalars().all()
    settings_by_key = {setting.key: setting for setting in rows}
    assert settings_by_key["audit_webhook_url"].is_encrypted is True
    assert settings_by_key["audit_webhook_custom_headers"].is_encrypted is True
    assert settings_by_key["audit_webhook_custom_payload"].is_encrypted is True
    assert settings_by_key["audit_webhook_payload_attribute"].is_encrypted is False
    assert settings_by_key["audit_webhook_verify_ssl"].is_encrypted is False
    assert settings_by_key["audit_webhook_custom_headers"].value != orjson.dumps(
        custom_headers, option=orjson.OPT_SORT_KEYS
    )


@pytest.mark.anyio
async def test_platform_audit_settings_can_clear(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)

    await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://example.com/platform-audit"
        )
    )
    settings = await service.update_audit_settings(
        PlatformAuditSettingsUpdate(audit_webhook_url=None)
    )

    assert settings.audit_webhook_url is None


@pytest.mark.anyio
async def test_platform_audit_url_clears_headers_on_origin_change(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)
    await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://audit.example.com/first",
            audit_webhook_custom_headers={"Authorization": "Bearer old-secret"},
        )
    )

    settings = await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://other.example.com/second"
        )
    )

    assert settings.audit_webhook_custom_headers is None


@pytest.mark.anyio
async def test_platform_audit_url_preserves_headers_on_same_origin(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)
    headers = {"Authorization": "Bearer retained-secret"}
    await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://audit.example.com/first",
            audit_webhook_custom_headers=headers,
        )
    )

    settings = await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://AUDIT.example.com:443/second?version=2"
        )
    )

    assert settings.audit_webhook_custom_headers == headers


@pytest.mark.anyio
async def test_platform_audit_url_accepts_explicit_cross_origin_headers(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)
    replacement_headers = {"Authorization": "Bearer replacement-secret"}
    await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://audit.example.com/first",
            audit_webhook_custom_headers={"Authorization": "Bearer old-secret"},
        )
    )

    settings = await service.update_audit_settings(
        PlatformAuditSettingsUpdate(
            audit_webhook_url="https://other.example.com/second",
            audit_webhook_custom_headers=replacement_headers,
        )
    )

    assert settings.audit_webhook_custom_headers == replacement_headers


@pytest.mark.anyio
async def test_concurrent_platform_audit_updates_cannot_misbind_headers(
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    first_at_write = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    second_at_write = asyncio.Event()
    release_second = asyncio.Event()
    first_task: asyncio.Task[None] | None = None
    second_task: asyncio.Task[None] | None = None
    original_upsert = AdminSettingsService._upsert_setting

    async def hold_first_upsert(
        service: AdminSettingsService,
        key: str,
        value: object,
    ) -> None:
        if service is first_service and not first_at_write.is_set():
            first_at_write.set()
            await release_first.wait()
        elif service is second_service and not second_at_write.is_set():
            second_at_write.set()
            await release_second.wait()
        await original_upsert(service, key, value)

    async def run_first_update() -> None:
        await first_service.update_audit_settings(
            PlatformAuditSettingsUpdate(
                audit_webhook_url="https://collector-b.example.com",
                audit_webhook_custom_headers={"Authorization": "Bearer origin-b"},
            )
        )

    async def run_second_update() -> None:
        second_started.set()
        await second_service.update_audit_settings(
            PlatformAuditSettingsUpdate(
                audit_webhook_url="https://collector-a.example.com/second"
            )
        )

    try:
        async with session_factory() as seed_session:
            seed_service = AdminSettingsService(
                session=seed_session,
                role=platform_role.model_copy(deep=True),
            )
            await seed_service.update_audit_settings(
                PlatformAuditSettingsUpdate(
                    audit_webhook_url="https://collector-a.example.com/first",
                    audit_webhook_custom_headers={"Authorization": "Bearer origin-a"},
                )
            )

        async with (
            session_factory() as first_session,
            session_factory() as second_session,
        ):
            first_service = AdminSettingsService(
                session=first_session,
                role=platform_role.model_copy(deep=True),
            )
            second_service = AdminSettingsService(
                session=second_session,
                role=platform_role.model_copy(deep=True),
            )
            monkeypatch.setattr(
                AdminSettingsService,
                "_upsert_setting",
                hold_first_upsert,
            )

            first_task = asyncio.create_task(run_first_update())
            await asyncio.wait_for(first_at_write.wait(), timeout=5)

            second_task = asyncio.create_task(run_second_update())
            await asyncio.wait_for(second_started.wait(), timeout=5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second_at_write.wait(), timeout=0.2)

            release_first.set()
            await first_task
            await asyncio.wait_for(second_at_write.wait(), timeout=5)
            release_second.set()
            await second_task

        async with session_factory() as verify_session:
            verify_service = AdminSettingsService(
                session=verify_session,
                role=platform_role.model_copy(deep=True),
            )
            settings = await verify_service.get_audit_settings()
            assert (
                settings.audit_webhook_url == "https://collector-a.example.com/second"
            )
            assert settings.audit_webhook_custom_headers is None
    finally:
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        pending_tasks = [
            task
            for task in (first_task, second_task)
            if task is not None and not task.done()
        ]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(PlatformSetting).where(
                    PlatformSetting.key.in_(AUDIT_SETTINGS_KEYS)
                )
            )
            await cleanup_session.commit()
        await engine.dispose()
