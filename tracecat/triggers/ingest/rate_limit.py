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
from typing import Literal

from tracecat import config
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

WINDOW_SECONDS = 60
_REDIS_TIMEOUT_SECONDS = 1.0
# A genuine source sees a confirmation once per subscribe. Confirmations get
# their own small window so replayed ones never use the notification budget.
CONFIRMATIONS_PER_MINUTE = 10


async def check_source_rate(source_id: uuid.UUID) -> int | None:
    """Count one notification against the source's cap.

    Returns:
        ``None`` if within the cap, else the seconds until the window resets.
    """
    return await _count(
        source_id,
        counter="rate",
        limit=config.TRACECAT__TRIGGERS_INGEST_RATE_LIMIT_PER_MINUTE,
    )


async def check_confirmation_rate(source_id: uuid.UUID) -> int | None:
    """Count one subscription confirmation against the source's own small cap.

    Returns:
        ``None`` if within the cap, else the seconds until the window resets.
    """
    return await _count(source_id, counter="confirm", limit=CONFIRMATIONS_PER_MINUTE)


async def _count(
    source_id: uuid.UUID, *, counter: Literal["rate", "confirm"], limit: int
) -> int | None:
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    key = f"triggers:ingest:{counter}:{source_id}:{window}"
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
    if int(count) <= limit:
        return None
    return max(1, math.ceil((window + 1) * WINDOW_SECONDS - now))
