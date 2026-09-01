from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from tracecat.temporal.search_attributes import ensure_error_owner_search_attribute


@pytest.mark.anyio
async def test_error_owner_search_attribute_readiness_uses_visibility_api() -> None:
    client = AsyncMock()

    await ensure_error_owner_search_attribute(client)

    client.count_workflows.assert_awaited_once_with(
        "TracecatErrorOwner IS NULL",
        rpc_timeout=timedelta(seconds=10),
    )


@pytest.mark.anyio
async def test_error_owner_search_attribute_readiness_fails_worker_startup() -> None:
    client = AsyncMock()
    client.count_workflows.side_effect = RPCError(
        "invalid query: TracecatErrorOwner is not a valid search attribute",
        RPCStatusCode.INVALID_ARGUMENT,
        b"",
    )

    with pytest.raises(RuntimeError, match="TracecatErrorOwner"):
        await ensure_error_owner_search_attribute(client)


@pytest.mark.anyio
async def test_error_owner_search_attribute_readiness_preserves_rpc_failure() -> None:
    client = AsyncMock()
    failure = RPCError(
        "visibility backend unavailable",
        RPCStatusCode.UNAVAILABLE,
        b"",
    )
    client.count_workflows.side_effect = failure

    with pytest.raises(RPCError) as exc_info:
        await ensure_error_owner_search_attribute(client)

    assert exc_info.value is failure
