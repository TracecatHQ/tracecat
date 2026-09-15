from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import ListSearchAttributesResponse
from temporalio.service import RPCError, RPCStatusCode
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
@pytest.mark.parametrize("denied_operation", ["list", "add"])
async def test_add_temporal_search_attributes_skips_permission_denied_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    denied_operation: str,
) -> None:
    error = RPCError("Operator access denied", RPCStatusCode.PERMISSION_DENIED, b"")
    list_attributes = AsyncMock(return_value=ListSearchAttributesResponse())
    add_attributes = AsyncMock()
    if denied_operation == "list":
        list_attributes.side_effect = error
    else:
        add_attributes.side_effect = error
    client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=list_attributes,
            add_search_attributes=add_attributes,
        )
    )
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))
    register = cast(Any, common.add_temporal_search_attributes).retry_with(
        wait=wait_none(),
    )

    await register()

    list_attributes.assert_awaited_once()
    assert add_attributes.await_count == (1 if denied_operation == "add" else 0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status", [RPCStatusCode.UNAVAILABLE, RPCStatusCode.UNAUTHENTICATED]
)
async def test_add_temporal_search_attributes_retries_other_rpc_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: RPCStatusCode,
) -> None:
    error = RPCError("Temporal RPC failed", status, b"")
    list_attributes = AsyncMock(side_effect=error)
    client = SimpleNamespace(
        operator_service=SimpleNamespace(list_search_attributes=list_attributes)
    )
    monkeypatch.setattr(common, "get_temporal_client", AsyncMock(return_value=client))
    register = cast(Any, common.add_temporal_search_attributes).retry_with(
        stop=stop_after_attempt(2),
        wait=wait_none(),
    )

    with pytest.raises(RPCError) as exc_info:
        await register()

    assert exc_info.value is error
    assert list_attributes.await_count == 2


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
