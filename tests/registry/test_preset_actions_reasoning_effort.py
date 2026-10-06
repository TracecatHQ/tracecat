"""Registry preset actions treat an empty argument as not provided.

A registry action receives an omitted argument and an explicit null as the
same ``None``, so the deprecated ``enable_thinking`` flag applies whenever
``reasoning_effort`` is empty. ``update_preset`` resets to the model default
only through ``clear_reasoning_effort``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.core import presets


@pytest.fixture
def sdk_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {"create": [], "update": []}

    async def create_preset(**kwargs: Any) -> dict[str, Any]:
        calls["create"].append(kwargs)
        return {}

    async def update_preset(slug: str, **kwargs: Any) -> dict[str, Any]:
        del slug
        calls["update"].append(kwargs)
        return {}

    ctx = MagicMock()
    ctx.agents.aio.create_preset = AsyncMock(side_effect=create_preset)
    ctx.agents.aio.update_preset = AsyncMock(side_effect=update_preset)
    monkeypatch.setattr(presets, "ctx", ctx)
    return calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"enable_thinking": False}, "off"),
        ({"enable_thinking": True}, None),
        ({"reasoning_effort": "high", "enable_thinking": False}, "high"),
        # An explicit null is indistinguishable from omission here.
        ({"reasoning_effort": None, "enable_thinking": False}, "off"),
    ],
)
async def test_create_preset_applies_legacy_flag_when_effort_is_empty(
    sdk_calls: dict[str, list[dict[str, Any]]],
    args: dict[str, Any],
    expected: str | None,
) -> None:
    await presets.create_preset(name="Triage preset", **args)

    assert sdk_calls["create"][0]["reasoning_effort"] == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"enable_thinking": False}, {"enable_thinking": False}),
        ({"enable_thinking": True}, {"enable_thinking": True}),
        (
            {"reasoning_effort": "low", "enable_thinking": True},
            {"reasoning_effort": "low"},
        ),
        (
            {"clear_reasoning_effort": True, "enable_thinking": False},
            {"reasoning_effort": None},
        ),
        ({}, {}),
    ],
)
async def test_update_preset_forwards_reasoning_inputs(
    sdk_calls: dict[str, list[dict[str, Any]]],
    args: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    await presets.update_preset(slug="triage-preset", **args)

    assert sdk_calls["update"] == [expected]


@pytest.mark.anyio
async def test_update_preset_rejects_effort_with_clear(
    sdk_calls: dict[str, list[dict[str, Any]]],
) -> None:
    with pytest.raises(ValueError, match="not both"):
        await presets.update_preset(
            slug="triage-preset", reasoning_effort="low", clear_reasoning_effort=True
        )
    assert sdk_calls["update"] == []
