from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from tenacity import stop_after_attempt, wait_none

from tracecat.api import common
from tracecat.api.app import lifespan
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
    assert TemporalSearchAttr.ERROR_OWNER.value in search_attributes


@pytest.mark.anyio
async def test_add_temporal_search_attributes_propagates_registration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_service = SimpleNamespace(
        add_search_attributes=AsyncMock(
            side_effect=RuntimeError("Temporal unavailable")
        )
    )
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))
    single_attempt_add = cast(Any, common.add_temporal_search_attributes).retry_with(
        stop=stop_after_attempt(1),
        wait=wait_none(),
    )

    with pytest.raises(RuntimeError, match="Temporal unavailable"):
        await single_attempt_add()


def test_api_lifespan_waits_for_temporal_search_attributes() -> None:
    source = inspect.getsource(lifespan)

    assert "await add_temporal_search_attributes()" in source
    assert "create_task(add_temporal_search_attributes())" not in source


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
    assert TemporalSearchAttr.ERROR_OWNER.value in search_attributes
