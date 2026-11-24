"""Which run first sent a web message, so every Retry mirrors under that run.

The channel mirror's delivery id is the run id: one origin per message means one post.
"""

from __future__ import annotations

import uuid

from botocore.exceptions import ClientError
from redis.exceptions import RedisError
from tenacity import RetryError

from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

PROMPT_ORIGIN_TTL_SECONDS = 24 * 60 * 60
"""How long a Retry can still find the run that first sent its message."""

# ClientError: the Redis URL's secret lookup on first use.
_REDIS_FAILURES = (RedisError, RuntimeError, RetryError, ClientError)


def prompt_origin_message_key(session_id: uuid.UUID, message_id: str) -> str:
    """Key of the first run for a web message's client id (hashed: clients set it)."""
    return (
        f"agent:prompt-origin:message:{session_id}:{uuid.uuid5(session_id, message_id)}"
    )


def prompt_origin_run_key(run_id: uuid.UUID) -> str:
    """Key of the first run for a run that retried a prompt."""
    return f"agent:prompt-origin:run:{run_id}"


async def resolve_prompt_origin(
    session_id: uuid.UUID,
    message_id: str,
    run_id: uuid.UUID,
    *,
    row_run_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """The run that first sent the message ``run_id`` now sends; None when it is the first.

    ``row_run_id`` is the run that wrote the prompt row Retry names after a reload;
    None means Retry resent the client id. On a Redis failure that run applies.
    """
    try:
        redis = await get_redis_client()
        if row_run_id is None:
            key = prompt_origin_message_key(session_id, message_id)
            if await redis.set_if_not_exists(
                key, str(run_id), expire_seconds=PROMPT_ORIGIN_TTL_SECONDS
            ):
                return None
            first = await redis.get(key)
        else:
            first = await redis.get(prompt_origin_run_key(row_run_id))
    except _REDIS_FAILURES as e:
        logger.warning("Prompt origin lookup failed", error=str(e))
        return row_run_id
    origin = uuid.UUID(first) if first else row_run_id
    if origin is not None and origin != run_id:
        try:
            await redis.set(
                prompt_origin_run_key(run_id),
                str(origin),
                expire_seconds=PROMPT_ORIGIN_TTL_SECONDS,
            )
        except _REDIS_FAILURES as e:
            # This send still mirrors under the origin; only a later Retry loses it.
            logger.warning("Prompt origin record failed", error=str(e))
    return origin
