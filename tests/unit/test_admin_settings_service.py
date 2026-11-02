from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.settings import service as admin_settings_service_module
from tracecat_ee.admin.settings.schemas import (
    PlatformAuditSettingsUpdate,
    PlatformSecuritySettingsUpdate,
)
from tracecat_ee.admin.settings.service import AdminSettingsService

from tracecat.audit.service import AuditService
from tracecat.auth import ip_allowlist_enforcement
from tracecat.auth.types import PlatformRole
from tracecat.db.models import OrganizationSetting, PlatformSetting
from tracecat.settings.schemas import IPAllowlist

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
def allowlist_loader_uses_test_session(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> Iterator[None]:
    @asynccontextmanager
    async def _auth_session() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        ip_allowlist_enforcement,
        "get_async_session_auth_context_manager",
        _auth_session,
    )
    ip_allowlist_enforcement.clear_ip_allowlist_cache()
    yield
    ip_allowlist_enforcement.clear_ip_allowlist_cache()


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
@pytest.mark.usefixtures("allowlist_loader_uses_test_session")
async def test_platform_security_settings_default_to_not_enforced(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)

    settings = await service.get_security_settings()

    assert settings.ip_allowlist_enabled is False
    assert settings.ip_allowlists == []
    ip_allowlist_enforcement.clear_ip_allowlist_cache()
    loaded = await ip_allowlist_enforcement.get_platform_admin_ip_allowlist()
    assert not loaded.enforced


@pytest.mark.anyio
@pytest.mark.usefixtures("allowlist_loader_uses_test_session")
async def test_platform_security_settings_round_trip_and_enforcement_load(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminSettingsService(session, platform_role)

    settings = await service.update_security_settings(
        PlatformSecuritySettingsUpdate(
            ip_allowlist_enabled=True,
            ip_allowlists=[
                IPAllowlist(
                    name="Ops VPN",
                    description="Platform operators",
                    cidrs=["203.0.113.77/24"],
                )
            ],
        )
    )

    assert settings.ip_allowlist_enabled is True
    assert settings.ip_allowlists == [
        IPAllowlist(
            name="Ops VPN", description="Platform operators", cidrs=["203.0.113.0/24"]
        )
    ]
    rows = (await session.execute(select(PlatformSetting))).scalars().all()
    keys = {row.key for row in rows}
    assert {
        "platform_admin_ip_allowlist_enabled",
        "platform_admin_ip_allowlists",
    } <= keys
    org_rows = (
        (
            await session.execute(
                select(OrganizationSetting).where(
                    OrganizationSetting.key.in_(
                        ["ip_allowlist_enabled", "ip_allowlists"]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert org_rows == []

    ip_allowlist_enforcement.clear_ip_allowlist_cache()
    loaded = await ip_allowlist_enforcement.get_platform_admin_ip_allowlist()
    assert loaded.enforced
    assert [n.with_prefixlen for n in loaded.networks] == ["203.0.113.0/24"]

    settings = await service.update_security_settings(
        PlatformSecuritySettingsUpdate(
            ip_allowlist_enabled=False, ip_allowlists=settings.ip_allowlists
        )
    )
    assert settings.ip_allowlist_enabled is False
    ip_allowlist_enforcement.clear_ip_allowlist_cache()
    assert not (
        await ip_allowlist_enforcement.get_platform_admin_ip_allowlist()
    ).enforced
    ip_allowlist_enforcement.clear_ip_allowlist_cache()


@pytest.mark.anyio
@pytest.mark.usefixtures("allowlist_loader_uses_test_session")
async def test_platform_admin_allowlist_ignores_malformed_rows(
    session: AsyncSession,
) -> None:
    session.add_all(
        [
            PlatformSetting(
                key="platform_admin_ip_allowlist_enabled",
                value=orjson.dumps(True),
                value_type="json",
                is_encrypted=False,
            ),
            PlatformSetting(
                key="platform_admin_ip_allowlists",
                value=b"not json",
                value_type="json",
                is_encrypted=False,
            ),
        ]
    )
    await session.commit()

    ip_allowlist_enforcement.clear_ip_allowlist_cache()
    loaded = await ip_allowlist_enforcement.get_platform_admin_ip_allowlist()
    assert not loaded.enforced
    ip_allowlist_enforcement.clear_ip_allowlist_cache()
