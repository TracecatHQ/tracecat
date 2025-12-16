"""Tests for remaining core.transform actions not covered elsewhere."""

from __future__ import annotations

import pytest
from tracecat_registry import ActionIsInterfaceError
from tracecat_registry.core import transform as core_transform


def test_reshape_returns_value() -> None:
    assert core_transform.reshape({"a": 1}) == {"a": 1}


def test_compact_drops_null_and_empty_string() -> None:
    assert core_transform.compact([None, "", 0, False, "x"]) == [0, False, "x"]


@pytest.mark.anyio
async def test_is_duplicate_uses_deduplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_deduplicate(*args, **kwargs):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(core_transform, "deduplicate", fake_deduplicate, raising=True)
    assert (
        await core_transform.is_duplicate(item={"id": 1}, keys=["id"], expire_seconds=1)
        is True
    )


@pytest.mark.anyio
async def test_is_duplicate_false_when_new(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_deduplicate(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [{"id": 1}]

    monkeypatch.setattr(core_transform, "deduplicate", fake_deduplicate, raising=True)
    assert (
        await core_transform.is_duplicate(item={"id": 1}, keys=["id"], expire_seconds=1)
        is False
    )


def test_scatter_is_interface() -> None:
    with pytest.raises(ActionIsInterfaceError):
        core_transform.scatter(collection=[1, 2, 3])  # type: ignore[misc]


def test_gather_is_interface() -> None:
    with pytest.raises(ActionIsInterfaceError):
        core_transform.gather(items="$.x")  # type: ignore[misc]
