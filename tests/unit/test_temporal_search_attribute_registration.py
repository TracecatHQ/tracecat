from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracecat.api import common
from tracecat.workflow.executions.enums import TemporalSearchAttr


@pytest.mark.anyio
async def test_add_temporal_search_attributes_registers_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_service = SimpleNamespace(add_search_attributes=AsyncMock())
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))

    await common.add_temporal_search_attributes()

    search_attributes = operator_service.add_search_attributes.await_args.args[
        0
    ].search_attributes
    assert TemporalSearchAttr.CORRELATION_ID.value in search_attributes


@pytest.mark.anyio
async def test_remove_temporal_search_attributes_removes_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_service = SimpleNamespace(remove_search_attributes=AsyncMock())
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))

    await common.remove_temporal_search_attributes()

    search_attributes = operator_service.remove_search_attributes.await_args.args[
        0
    ].search_attributes
    assert TemporalSearchAttr.CORRELATION_ID.value in search_attributes
