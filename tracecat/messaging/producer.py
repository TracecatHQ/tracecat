from __future__ import annotations

from typing import Any

import orjson
from aio_pika import Channel, DeliveryMode, ExchangeType, Message
from aio_pika.pool import Pool

from tracecat.logger import standard_logger
from tracecat.messaging.common import RABBITMQ_RUNNER_EVENTS_EXCHANGE

logger = standard_logger(__name__)


async def event_producer(
    channel: Channel,
    *,
    exchange: str,
    payload: dict[str, Any],
    routing_keys: list[str],
) -> None:
    ex = await channel.declare_exchange(exchange, ExchangeType.DIRECT)
    message = Message(orjson.dumps(payload), delivery_mode=DeliveryMode.PERSISTENT)

    for routing_key in routing_keys:
        await ex.publish(message, routing_key=routing_key)
        logger.debug(f" [x] {routing_key = } Sent {message.body!r}")


async def publish(
    pool: Pool[Channel],
    *,
    routing_keys: list[str],
    payload: dict[str, Any],
) -> None:
    async with pool.acquire() as channel:
        await event_producer(
            channel=channel,
            exchange=RABBITMQ_RUNNER_EVENTS_EXCHANGE,
            payload=payload,
            routing_keys=routing_keys,
        )
