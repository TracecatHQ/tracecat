from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aio_pika import Channel, ExchangeType
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.pool import Pool

from tracecat.logger import standard_logger
from tracecat.messaging.common import RABBITMQ_RUNNER_EVENTS_EXCHANGE

logger = standard_logger(__name__)


@asynccontextmanager
async def prepare_queue(*, channel: Channel, exchange: str, routing_keys: list[str]):
    queue = None
    try:
        await channel.set_qos(prefetch_count=1)

        # Declare an exchange
        ex = await channel.declare_exchange(
            exchange,
            ExchangeType.DIRECT,
        )

        # Declaring random queue
        queue = await channel.declare_queue(durable=True, exclusive=True)
        for routing_key in routing_keys:
            await queue.bind(ex, routing_key=routing_key)
        yield queue
    except Exception as e:
        logger.error(f"Error in prepare_exchange: {e}", exc_info=True)
    finally:
        # Cleanup
        logger.info(f"Cleaning up exchange {exchange!r}")
        if queue:
            for routing_key in routing_keys:
                await queue.unbind(ex, routing_key=routing_key)
            await queue.delete()


async def event_consumer(
    channel: Channel,
    *,
    exchange: str,
    routing_keys: list[str],
) -> AsyncGenerator[AbstractIncomingMessage, None]:
    async with prepare_queue(
        channel=channel,
        exchange=exchange,
        routing_keys=routing_keys,
    ) as queue:
        async with queue.iterator() as iterator:
            message: AbstractIncomingMessage
            async for message in iterator:
                async with message.process():
                    yield message


async def subscribe(
    pool: Pool[Channel], *, routing_keys: list[str]
) -> AsyncGenerator[str, None]:
    """Subscribe to events for a user.

    The routing key is the user_id.
    Users only receive events that are published to their user_id.
    """
    try:
        async with pool.acquire() as channel:
            async for event in event_consumer(
                channel=channel,
                exchange=RABBITMQ_RUNNER_EVENTS_EXCHANGE,
                routing_keys=routing_keys,
            ):
                out = str(event.body + b"\n", "utf-8")
                yield out
    except Exception as e:
        logger.error(f"Error in event subscription: {e}", exc_info=True)
    finally:
        logger.info("Closing event subscription")
