"""Redis failures remain visible when search results need no saved cursor."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from tracecat.search.cursors import WindowStore
from tracecat.search.types import SearchScope


@pytest.mark.anyio
async def test_window_availability_check_propagates_redis_failure():
    client = AsyncMock()
    client.ping.side_effect = RedisConnectionError("synthetic Redis outage")
    with patch(
        "tracecat.search.cursors.RedisClient._get_client",
        new=AsyncMock(return_value=client),
    ):
        with pytest.raises(RedisConnectionError):
            await WindowStore(SearchScope(uuid4(), uuid4())).check_available()
