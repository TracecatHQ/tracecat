"""Per-source cap on ingested notifications, v1's only loop guard.

A fixed one-minute window counted in Redis, the same scheme as the MCP token
endpoint's per-source cap. Redis being down fails open: the cap guards
against runaway loops, and refusing every event during a Redis outage would
cost more than it saves.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid

from tracecat import config
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

WINDOW_SECONDS = 60
_REDIS_TIMEOUT_SECONDS = 1.0


def _window_key(source_id: uuid.UUID, window: int) -> str:
    return f"triggers:ingest:rate:{source_id}:{window}"


async def check_source_rate(source_id: uuid.UUID) -> int | None:
    """Count one notification against the source's cap.

    Returns:
        ``None`` if within the cap, else the seconds until the window resets.
    """
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    key = _window_key(source_id, window)
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
            redis_client = await get_redis_client()
            client = await redis_client._get_client()
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS * 2, nx=True)
            count, _ = await pipe.execute()
    except Exception as e:
        logger.warning(
            "Trigger ingest rate cap unavailable, allowing",
            source_id=str(source_id),
            error_type=type(e).__name__,
        )
        return None
    if int(count) <= config.TRACECAT__TRIGGERS_INGEST_RATE_LIMIT_PER_MINUTE:
        return None
    return max(1, math.ceil((window + 1) * WINDOW_SECONDS - now))
