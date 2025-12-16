"""Tests for ai.agent preset UDFs using the registry SDK client."""

from __future__ import annotations

import pytest
from tracecat_registry.core import presets as core_presets
from tracecat_registry.sdk.client import TracecatClient


@pytest.mark.anyio
async def test_create_preset_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/presets"
        assert params is None
        assert json == {
            "name": "Security Analyst",
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "slug": "security-analyst",
            "description": "d",
            "instructions": "i",
            "base_url": "https://example.com",
            "output_type": "str",
            "actions": ["core.cases.create_case"],
        }
        return {"id": "preset-1", "slug": "security-analyst"}

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    result = await core_presets.create_preset(
        name="Security Analyst",
        model_name="gpt-4o-mini",
        model_provider="openai",
        slug="security-analyst",
        description="d",
        instructions="i",
        base_url="https://example.com",
        output_type="str",
        actions=["core.cases.create_case"],
    )
    assert result["slug"] == "security-analyst"


@pytest.mark.anyio
async def test_create_preset_omits_unset_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/presets"
        assert json == {
            "name": "N",
            "model_name": "m",
            "model_provider": "p",
        }
        return {"id": "preset-1"}

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    assert await core_presets.create_preset(
        name="N",
        model_name="m",
        model_provider="p",
        slug=None,
        description=None,
        instructions=None,
        base_url=None,
        output_type=None,
        actions=None,
    ) == {"id": "preset-1"}


@pytest.mark.anyio
async def test_get_preset_gets_by_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/presets/by-slug/security-analyst"
        return {"id": "preset-1", "slug": "security-analyst"}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_presets.get_preset("security-analyst") == {
        "id": "preset-1",
        "slug": "security-analyst",
    }


@pytest.mark.anyio
async def test_list_presets_gets_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/presets"
        return [{"slug": "a"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_presets.list_presets() == [{"slug": "a"}]


@pytest.mark.anyio
async def test_update_preset_patches_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_patch(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/presets/by-slug/security-analyst"
        assert json == {
            "name": "New Name",
            "slug": "security-analyst-v2",
            "actions": ["core.cases.list_cases"],
        }
        return {"ok": True}

    monkeypatch.setattr(TracecatClient, "patch", fake_patch, raising=True)

    result = await core_presets.update_preset(
        slug="security-analyst",
        name="New Name",
        new_slug="security-analyst-v2",
        actions=["core.cases.list_cases"],
    )
    assert result == {"ok": True}


@pytest.mark.anyio
async def test_delete_preset_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/presets/by-slug/security-analyst"
        return None

    monkeypatch.setattr(TracecatClient, "delete", fake_delete, raising=True)
    assert await core_presets.delete_preset("security-analyst") is None
