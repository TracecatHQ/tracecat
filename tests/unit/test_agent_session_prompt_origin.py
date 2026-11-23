"""A Retry finds the run that first sent its web message, and degrades on Redis failure."""

from __future__ import annotations

import uuid

import pytest
from redis.exceptions import RedisError
from tenacity import RetryError

from tracecat.agent.session import prompt_origin
from tracecat.agent.session.prompt_origin import (
    prompt_origin_message_key,
    resolve_prompt_origin,
)

pytestmark = pytest.mark.anyio


class _BrokenRedis:
    """Fails every call, as the client does once its retries run out."""

    def __init__(self, error: BaseException, *, reads: dict[str, str] | None = None):
        self.error = error
        self.reads = reads

    async def set_if_not_exists(self, *_: object, **__: object) -> bool:
        raise self.error

    async def get(self, key: str) -> str | None:
        if self.reads is not None:
            return self.reads.get(key)
        raise self.error

    async def set(self, *_: object, **__: object) -> bool:
        raise self.error


def _use(monkeypatch: pytest.MonkeyPatch, client: _BrokenRedis) -> None:
    async def get_redis_client() -> _BrokenRedis:
        return client

    monkeypatch.setattr(prompt_origin, "get_redis_client", get_redis_client)


@pytest.mark.parametrize(
    "error",
    [RedisError("down"), RetryError(last_attempt=None)],  # pyright: ignore[reportArgumentType]
)
async def test_a_redis_failure_falls_back_to_the_rows_own_run(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    _use(monkeypatch, _BrokenRedis(error))
    row_run = uuid.uuid4()

    assert (
        await resolve_prompt_origin(uuid.uuid4(), "m", uuid.uuid4(), row_run_id=row_run)
        == row_run
    )
    assert (
        await resolve_prompt_origin(uuid.uuid4(), "m", uuid.uuid4(), row_run_id=None)
        is None
    )


async def test_a_failed_record_still_returns_the_origin_it_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, first_run = uuid.uuid4(), uuid.uuid4()
    reads = {prompt_origin_message_key(session_id, "m"): str(first_run)}

    class _Taken(_BrokenRedis):
        async def set_if_not_exists(self, *_: object, **__: object) -> bool:
            return False

    _use(monkeypatch, _Taken(RedisError("down"), reads=reads))

    assert (
        await resolve_prompt_origin(session_id, "m", uuid.uuid4(), row_run_id=None)
        == first_run
    )


def test_a_client_message_id_never_lands_raw_in_a_key() -> None:
    key = prompt_origin_message_key(uuid.uuid4(), "x" * 10_000)

    assert len(key) < 120
