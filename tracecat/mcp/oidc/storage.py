"""Redis-backed stores for the internal OIDC issuer.

Stores authorization codes, resume transactions, and JTI records with
automatic TTL enforcement and optional Fernet encryption at rest.
"""

from __future__ import annotations

import orjson
from cryptography.fernet import Fernet
from redis.asyncio import Redis as AsyncRedis

from tracecat.config import REDIS_URL, TRACECAT__DB_ENCRYPTION_KEY
from tracecat.logger import logger
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc.schemas import AuthCodeData, ResumeTransaction

_KEY_PREFIX = "mcp-oidc"


class _EncryptedRedis:
    """Thin wrapper that Fernet-encrypts values before storing in Redis.

    If no encryption key is configured, values are stored as plain JSON.
    """

    def __init__(self, redis: AsyncRedis, fernet: Fernet | None) -> None:  # type: ignore[type-arg]
        self._redis = redis
        self._fernet = fernet

    def _encode(self, data: bytes) -> bytes:
        if self._fernet is not None:
            return self._fernet.encrypt(data)
        return data

    def _decode(self, data: bytes) -> bytes:
        if self._fernet is not None:
            return self._fernet.decrypt(data)
        return data

    async def set(self, key: str, value: bytes, *, ttl: int) -> None:
        """Store an encrypted value with TTL (seconds)."""
        await self._redis.set(key, self._encode(value), ex=ttl)

    async def get(self, key: str) -> bytes | None:
        """Retrieve and decrypt a value, returning ``None`` if absent."""
        raw: bytes | None = await self._redis.get(key)
        if raw is None:
            return None
        return self._decode(raw)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        await self._redis.delete(key)

    async def incr_with_ttl(self, key: str, ttl: int) -> int:
        """Increment a counter, setting TTL on first creation.

        Returns:
            The counter value after increment.
        """
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        results = await pipe.execute()
        return results[0]


def _build_store() -> _EncryptedRedis:
    """Create an encrypted Redis store for OIDC issuer state."""
    redis = AsyncRedis.from_url(REDIS_URL, decode_responses=False)
    fernet: Fernet | None = None
    if TRACECAT__DB_ENCRYPTION_KEY:
        fernet = Fernet(TRACECAT__DB_ENCRYPTION_KEY)
    else:
        logger.warning(
            "TRACECAT__DB_ENCRYPTION_KEY is not set; "
            "MCP OIDC issuer state will be stored unencrypted in Redis"
        )
    return _EncryptedRedis(redis, fernet)


# Module-level singleton, created on first import.
_store: _EncryptedRedis | None = None


def _get_store() -> _EncryptedRedis:
    global _store  # noqa: PLW0603
    if _store is None:
        _store = _build_store()
    return _store


# --- Auth code operations ---


def _code_key(code: str) -> str:
    return f"{_KEY_PREFIX}:codes:{code}"


async def store_auth_code(data: AuthCodeData) -> None:
    """Store an authorization code with automatic TTL."""
    store = _get_store()
    key = _code_key(data.code)
    payload = orjson.dumps(data.model_dump(mode="json"))
    await store.set(key, payload, ttl=oidc_config.AUTH_CODE_LIFETIME_SECONDS)


async def load_and_delete_auth_code(code: str) -> AuthCodeData | None:
    """Atomically load and delete an authorization code (one-time use).

    Returns:
        The code data if found and valid, ``None`` otherwise.
    """
    store = _get_store()
    key = _code_key(code)
    raw = await store.get(key)
    if raw is None:
        return None
    # Delete immediately to enforce one-time use.
    await store.delete(key)
    return AuthCodeData.model_validate_json(raw)


# --- Resume transaction operations ---


def _resume_key(txn_id: str) -> str:
    return f"{_KEY_PREFIX}:resume:{txn_id}"


async def store_resume_transaction(txn: ResumeTransaction) -> None:
    """Store a resume transaction with automatic TTL."""
    store = _get_store()
    key = _resume_key(txn.transaction_id)
    payload = orjson.dumps(txn.model_dump(mode="json"))
    await store.set(key, payload, ttl=oidc_config.RESUME_TRANSACTION_LIFETIME_SECONDS)


async def load_and_delete_resume_transaction(
    txn_id: str,
) -> ResumeTransaction | None:
    """Atomically load and delete a resume transaction (one-time use).

    Returns:
        The transaction data if found, ``None`` otherwise.
    """
    store = _get_store()
    key = _resume_key(txn_id)
    raw = await store.get(key)
    if raw is None:
        return None
    await store.delete(key)
    return ResumeTransaction.model_validate_json(raw)


# --- JTI tracking ---


def _jti_key(jti: str) -> str:
    return f"{_KEY_PREFIX}:jti:{jti}"


async def store_jti(jti: str) -> None:
    """Record a JTI to enable future revocation checks."""
    store = _get_store()
    key = _jti_key(jti)
    await store.set(key, b"1", ttl=oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS)


# --- Rate limiting ---


def _rate_limit_key(ip: str) -> str:
    return f"{_KEY_PREFIX}:rate:{ip}"


async def check_token_rate_limit(ip: str) -> bool:
    """Check and increment the per-IP rate limit for the token endpoint.

    Returns:
        ``True`` if the request is within the limit, ``False`` if exceeded.
    """
    store = _get_store()
    key = _rate_limit_key(ip)
    count = await store.incr_with_ttl(key, ttl=60)
    return count <= oidc_config.TOKEN_RATE_LIMIT_PER_MINUTE
