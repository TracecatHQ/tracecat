"""Bounded, authenticated Redis result windows containing references, never text."""

import hashlib
import hmac
import secrets
from collections.abc import Awaitable
from typing import cast
from uuid import UUID

from pydantic import BaseModel, Field

from tracecat.redis.client import RedisClient
from tracecat.search.types import SearchError, SearchErrorCode, SearchScope


class RankedReference(BaseModel):
    """One fully published row and its winning chunk."""

    row_id: UUID
    chunk_id: UUID
    revision: int
    score: float


class SearchWindow(BaseModel):
    """Immutable top-row snapshot; authentication material stays in Redis."""

    context: str
    references: list[RankedReference] = Field(max_length=100)
    capped: bool
    secret: str = Field(default_factory=lambda: secrets.token_hex(32))


_SAVE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
while redis.call('ZCARD', KEYS[1]) >= 32 do
  local oldest = redis.call('ZPOPMIN', KEYS[1], 1)
  redis.call('DEL', oldest[1])
end
redis.call('SET', KEYS[2], ARGV[1], 'EX', 300)
redis.call('ZADD', KEYS[1], now + 300, KEYS[2])
redis.call('EXPIRE', KEYS[1], 300)
return 1
"""


class WindowStore:
    """Five-minute windows, capped at 32 per workspace; reads never extend TTL."""

    def __init__(self, scope: SearchScope):
        self.prefix = f"search:{{{scope.organization_id}:{scope.workspace_id}}}:"

    async def check_available(self) -> None:
        """Require Redis availability without occupying a pagination slot."""
        client = await RedisClient()._get_client()
        await cast(Awaitable[bool], client.ping())

    async def save(self, window: SearchWindow) -> str:
        """Persist a window atomically with workspace eviction bookkeeping."""
        identifier = secrets.token_hex(16)
        client = await RedisClient()._get_client()
        await cast(
            Awaitable[object],
            client.eval(
                _SAVE,
                2,
                self.prefix + "windows",
                self.prefix + identifier,
                window.model_dump_json(),
            ),
        )
        return identifier

    @staticmethod
    def cursor(identifier: str, window: SearchWindow, position: int) -> str:
        """Authenticate the window and position without exposing result state."""
        payload = f"{identifier}.{position}"
        signature = hmac.new(
            bytes.fromhex(window.secret), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"

    async def load(self, cursor: str, context: str) -> tuple[str, SearchWindow, int]:
        """Reject missing, tampered, expired or differently authorized windows."""
        try:
            identifier, position_text, _signature = cursor.split(".")
            if len(identifier) != 32 or any(
                c not in "0123456789abcdef" for c in identifier
            ):
                raise ValueError
            position = int(position_text)
            client = await RedisClient()._get_client()
            raw = await client.get(self.prefix + identifier)
            if raw is None:
                raise ValueError
            window = SearchWindow.model_validate_json(raw)
            expected = self.cursor(identifier, window, position)
            if (
                not hmac.compare_digest(expected.encode(), cursor.encode())
                or window.context != context
                or not 0 <= position < len(window.references)
            ):
                raise ValueError
            return identifier, window, position
        except ValueError:
            pass
        raise SearchError(SearchErrorCode.INVALID_CURSOR)
