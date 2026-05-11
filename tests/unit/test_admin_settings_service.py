from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.settings.schemas import PlatformAuditSettingsUpdate
from tracecat_ee.admin.settings.service import AdminSettingsService

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
) -> None:
    service = AdminSettingsService(session, platform_role)
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
