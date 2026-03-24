"""Tests for registry preset helper wrappers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry.core.presets import update_preset as core_update_preset
from tracecat_registry.sdk.agents import AgentsClient


@pytest.mark.anyio
async def test_core_update_preset_preserves_explicit_null_fallback_models() -> None:
    mock_agents = MagicMock()
    mock_agents.update_preset = AsyncMock(return_value={"ok": True})
    mock_context = SimpleNamespace(agents=mock_agents)

    with patch("tracecat_registry.core.presets.get_context", return_value=mock_context):
        result = await core_update_preset(
            slug="triage",
            fallback_models=None,
        )

    assert result == {"ok": True}
    mock_agents.update_preset.assert_awaited_once_with(
        "triage",
        fallback_models=None,
    )


@pytest.mark.anyio
async def test_sdk_update_preset_preserves_explicit_null_fallback_models() -> None:
    mock_http_client = MagicMock()
    mock_http_client.patch = AsyncMock(return_value={"ok": True})
    client = AgentsClient(mock_http_client)

    result = await client.update_preset(
        "triage",
        fallback_models=None,
    )

    assert result == {"ok": True}
    mock_http_client.patch.assert_awaited_once_with(
        "/agent/presets/by-slug/triage",
        json={"fallback_models": None},
    )
