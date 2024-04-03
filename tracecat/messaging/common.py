from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from aio_pika import Channel
from aio_pika.abc import AbstractRobustConnection
from aio_pika.pool import Pool

from tracecat.logger import standard_logger

logger = standard_logger(__name__)
RABBITMQ_URI = os.environ.get("RABBITMQ_URI", "amqp://guest:guest@localhost/")
RABBITMQ_RUNNER_EVENTS_EXCHANGE = "runner_events"


async def get_connection() -> AbstractRobustConnection:
    logger.info(f"Connecting to RabbitMQ at {RABBITMQ_URI}")
    return await aio_pika.connect_robust(RABBITMQ_URI)


@asynccontextmanager
async def use_channel_pool() -> AsyncIterator[Pool[Channel]]:
    rabbitmq_conn_pool = Pool(get_connection, max_size=2)

    async def get_channel() -> Channel:
        async with rabbitmq_conn_pool.acquire() as connection:
            return await connection.channel()

    rabbitmq_channel_pool = Pool(get_channel)
    try:
        logger.info("Created RabbitMQ channel pool")
        yield rabbitmq_channel_pool
    finally:
        if rabbitmq_conn_pool is not None:
            await rabbitmq_conn_pool.close()
        if rabbitmq_channel_pool is not None:
            await rabbitmq_channel_pool.close()
