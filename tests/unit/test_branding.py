"""Tests for organization branding settings and resolution."""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.branding import service as branding_service
from tracecat.branding.schemas import BrandingRead
from tracecat.db.models import Organization
from tracecat.settings.schemas import BRANDING_LOGO_MAX_BYTES, BrandingSettingsUpdate

PNG_LOGO = "data:image/png;base64," + base64.b64encode(b"\x89PNG logo").decode()


def test_branding_update_accepts_valid_values() -> None:
    params = BrandingSettingsUpdate(
        branding_logo=PNG_LOGO,
        branding_primary_color_light="#5E6AD2",
        branding_primary_color_dark="#8b93ff",
        branding_terms_enabled=False,
    )
    assert params.branding_logo == PNG_LOGO
    assert params.branding_terms_enabled is False


@pytest.mark.parametrize("color", ["5E6AD2", "#5E6", "#GGGGGG", "red", "#5E6AD2FF"])
def test_branding_update_rejects_invalid_hex(color: str) -> None:
    with pytest.raises(ValidationError):
        BrandingSettingsUpdate(branding_primary_color_light=color)


@pytest.mark.parametrize(
    "logo",
    [
        "https://example.com/logo.png",
        "data:text/html;base64,PGgxPmhpPC9oMT4=",
        "data:image/png;base64,not base64!",
        pytest.param(
            "data:image/png;base64,"
            + base64.b64encode(b"x" * (BRANDING_LOGO_MAX_BYTES + 1)).decode(),
            id="oversized-logo",
        ),
    ],
)
def test_branding_update_rejects_invalid_logo(logo: str) -> None:
    with pytest.raises(ValidationError):
        BrandingSettingsUpdate(branding_logo=logo)


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch, *, entitled: bool, stored: dict[str, Any]
) -> None:
    async def fake_entitled(*_args: Any, **_kwargs: Any) -> bool:
        return entitled

    async def fake_get_setting(key: str, **_kwargs: Any) -> Any:
        return stored.get(key)

    monkeypatch.setattr(branding_service, "is_org_entitled", fake_entitled)
    monkeypatch.setattr(
        branding_service, "get_setting_from_bypass_session", fake_get_setting
    )


STORED = {
    "branding_logo": PNG_LOGO,
    "branding_primary_color_light": "#5E6AD2",
    "branding_primary_color_dark": "#8B93FF",
    "branding_terms_enabled": False,
}


@pytest.mark.anyio
async def test_effective_branding_requires_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, entitled=False, stored=STORED)
    branding = await branding_service.get_effective_branding(
        session=None,  # pyright: ignore[reportArgumentType]
        org_id=uuid.uuid4(),
    )
    assert branding == BrandingRead()
    assert branding.terms_enabled is True


@pytest.mark.anyio
async def test_effective_branding_returns_stored_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, entitled=True, stored=STORED)
    branding = await branding_service.get_effective_branding(
        session=None,  # pyright: ignore[reportArgumentType]
        org_id=uuid.uuid4(),
    )
    assert branding == BrandingRead(
        logo=PNG_LOGO,
        primary_color_light="#5E6AD2",
        primary_color_dark="#8B93FF",
        terms_enabled=False,
    )


@pytest.mark.anyio
async def test_effective_branding_ignores_corrupt_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(
        monkeypatch,
        entitled=True,
        stored={**STORED, "branding_primary_color_light": "javascript:alert(1)"},
    )
    branding = await branding_service.get_effective_branding(
        session=None,  # pyright: ignore[reportArgumentType]
        org_id=uuid.uuid4(),
    )
    assert branding == BrandingRead()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_resolve_org_id_by_slug_skips_inactive(session: AsyncSession) -> None:
    active = Organization(
        id=uuid.uuid4(), name="Active", slug=f"a-{uuid.uuid4().hex[:8]}", is_active=True
    )
    inactive = Organization(
        id=uuid.uuid4(),
        name="Inactive",
        slug=f"i-{uuid.uuid4().hex[:8]}",
        is_active=False,
    )
    session.add_all([active, inactive])
    await session.commit()

    assert await branding_service.resolve_org_id_by_slug(session, active.slug) == (
        active.id
    )
    assert await branding_service.resolve_org_id_by_slug(session, inactive.slug) is None
    assert await branding_service.resolve_org_id_by_slug(session, "missing") is None
