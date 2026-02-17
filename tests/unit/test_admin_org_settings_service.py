"""Tests for superuser encrypted organization setting reset behavior."""

from __future__ import annotations

import uuid

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.organizations.service import AdminOrgService

from tracecat import config
from tracecat.auth.types import PlatformRole
from tracecat.db.models import Organization, OrganizationSetting
from tracecat.secrets.encryption import decrypt_value, encrypt_value

pytestmark = pytest.mark.usefixtures("db")


def _encryption_key() -> str:
    key = config.TRACECAT__DB_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("TRACECAT__DB_ENCRYPTION_KEY is not set")
    return key


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
async def org_a(session: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Org A",
        slug=f"org-a-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.commit()
    return organization


@pytest.mark.anyio
async def test_reset_encrypted_org_setting_success(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    key = _encryption_key()
    encrypted_value = encrypt_value(
        orjson.dumps("https://example.com/old"),
        key=key,
    )
    setting = OrganizationSetting(
        organization_id=org_a.id,
        key="audit_webhook_url",
        value=encrypted_value,
        value_type="json",
        is_encrypted=True,
    )
    session.add(setting)
    await session.commit()

    service = AdminOrgService(session, role=platform_role)
    result = await service.reset_encrypted_org_setting(
        org_a.id,
        "audit_webhook_url",
        value="https://example.com/new",
    )

    assert result.organization_id == org_a.id
    assert result.key == "audit_webhook_url"
    assert result.is_encrypted is True

    refreshed = await session.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == org_a.id,
            OrganizationSetting.key == "audit_webhook_url",
        )
    )
    assert refreshed is not None
    decrypted = decrypt_value(refreshed.value, key=key)
    assert orjson.loads(decrypted) == "https://example.com/new"


@pytest.mark.anyio
async def test_reset_encrypted_org_setting_succeeds_with_invalid_ciphertext(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    key = _encryption_key()
    setting = OrganizationSetting(
        organization_id=org_a.id,
        key="audit_webhook_custom_headers",
        value=b"invalid-ciphertext",
        value_type="json",
        is_encrypted=True,
    )
    session.add(setting)
    await session.commit()

    service = AdminOrgService(session, role=platform_role)
    await service.reset_encrypted_org_setting(
        org_a.id,
        "audit_webhook_custom_headers",
        value={"X-Test": "new-value"},
    )

    refreshed = await session.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == org_a.id,
            OrganizationSetting.key == "audit_webhook_custom_headers",
        )
    )
    assert refreshed is not None
    decrypted = decrypt_value(refreshed.value, key=key)
    assert orjson.loads(decrypted) == {"X-Test": "new-value"}


@pytest.mark.anyio
async def test_reset_encrypted_org_setting_rejects_non_encrypted_setting(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    setting = OrganizationSetting(
        organization_id=org_a.id,
        key="saml_enabled",
        value=orjson.dumps(True),
        value_type="json",
        is_encrypted=False,
    )
    session.add(setting)
    await session.commit()

    service = AdminOrgService(session, role=platform_role)
    with pytest.raises(ValueError, match="not encrypted"):
        await service.reset_encrypted_org_setting(org_a.id, "saml_enabled", value=False)


@pytest.mark.anyio
async def test_reset_encrypted_org_setting_not_found(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)
    with pytest.raises(ValueError, match="not found"):
        await service.reset_encrypted_org_setting(
            org_a.id,
            "audit_webhook_url",
            value="https://example.com/new",
        )
