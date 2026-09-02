from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import ListSearchAttributesResponse
from tenacity import stop_after_attempt, wait_none

from tracecat.api import common
from tracecat.api.app import lifespan
from tracecat.workflow.executions.enums import TemporalSearchAttr


@pytest.mark.anyio
async def test_add_temporal_search_attributes_registers_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(return_value=ListSearchAttributesResponse()),
        add_search_attributes=AsyncMock(),
    )
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))

    await common.add_temporal_search_attributes()

    search_attributes = operator_service.add_search_attributes.await_args.args[
        0
    ].search_attributes
    assert TemporalSearchAttr.CORRELATION_ID.value in search_attributes
    assert TemporalSearchAttr.ERROR_OWNER.value in search_attributes


@pytest.mark.anyio
async def test_add_temporal_search_attributes_registers_only_missing_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_attributes = {
        attr.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
        for attr in TemporalSearchAttr
        if attr is not TemporalSearchAttr.ERROR_OWNER
    }
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(
            return_value=ListSearchAttributesResponse(
                custom_attributes=existing_attributes
            )
        ),
        add_search_attributes=AsyncMock(),
    )
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))

    await common.add_temporal_search_attributes()

    request = operator_service.add_search_attributes.await_args.args[0]
    assert dict(request.search_attributes) == {
        TemporalSearchAttr.ERROR_OWNER.value: (
            IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
        )
    }


@pytest.mark.anyio
async def test_add_temporal_search_attributes_skips_registered_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_attributes = {
        attr.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
        for attr in TemporalSearchAttr
    }
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(
            return_value=ListSearchAttributesResponse(
                custom_attributes=existing_attributes
            )
        ),
        add_search_attributes=AsyncMock(),
    )
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))

    await common.add_temporal_search_attributes()

    operator_service.add_search_attributes.assert_not_awaited()


@pytest.mark.anyio
async def test_add_temporal_search_attributes_rejects_wrong_registered_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(
            return_value=ListSearchAttributesResponse(
                custom_attributes={
                    TemporalSearchAttr.ERROR_OWNER.value: (
                        IndexedValueType.INDEXED_VALUE_TYPE_TEXT
                    )
                }
            )
        ),
        add_search_attributes=AsyncMock(),
    )
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))
    single_attempt_add = cast(Any, common.add_temporal_search_attributes).retry_with(
        stop=stop_after_attempt(1),
        wait=wait_none(),
    )

    with pytest.raises(RuntimeError, match="TracecatErrorOwner"):
        await single_attempt_add()

    operator_service.add_search_attributes.assert_not_awaited()


@pytest.mark.anyio
async def test_add_temporal_search_attributes_propagates_registration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(return_value=ListSearchAttributesResponse()),
        add_search_attributes=AsyncMock(
            side_effect=RuntimeError("Temporal unavailable")
        ),
    )
    client = SimpleNamespace(operator_service=operator_service)
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))
    single_attempt_add = cast(Any, common.add_temporal_search_attributes).retry_with(
        stop=stop_after_attempt(1),
        wait=wait_none(),
    )

    with pytest.raises(RuntimeError, match="Temporal unavailable"):
        await single_attempt_add()


def test_api_lifespan_supervises_temporal_search_attribute_registration() -> None:
    source = inspect.getsource(lifespan)

    assert "supervisor.spawn(\n        add_temporal_search_attributes()" in source
    assert 'name="temporal_search_attribute_registration"' in source
    assert "await add_temporal_search_attributes()" not in source


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
