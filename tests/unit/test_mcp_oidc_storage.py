"""Unit tests for tracecat.mcp.oidc.storage — Redis-backed OIDC issuer state."""

from __future__ import annotations

import time
import uuid

import pytest
from cryptography.fernet import Fernet

from tracecat.mcp.oidc import storage
from tracecat.mcp.oidc.schemas import AuthCodeData, ResumeTransaction

# ---------------------------------------------------------------------------
# In-memory Redis substitute
# ---------------------------------------------------------------------------


class _InMemoryRedis:
    """Minimal async-compatible dict backend for _EncryptedRedis tests."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def set(self, name: str, value: bytes, *, ex: int | None = None) -> None:
        self._data[name] = value

    async def get(self, name: str) -> bytes | None:
        return self._data.get(name)

    async def delete(self, *names: str) -> None:
        for name in names:
            self._data.pop(name, None)

    def pipeline(self) -> _InMemoryPipeline:
        return _InMemoryPipeline(self)

    def raw_get(self, name: str) -> bytes | None:
        """Direct access for assertions about encrypted vs plaintext."""
        return self._data.get(name)


class _InMemoryPipeline:
    def __init__(self, redis: _InMemoryRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def incr(self, name: str) -> _InMemoryPipeline:
        self._ops.append(("incr", (name,)))
        return self

    def expire(self, name: str, ttl: int, *, nx: bool = False) -> _InMemoryPipeline:
        self._ops.append(("expire", (name, ttl, nx)))
        return self

    async def execute(self) -> list:
        results = []
        for op, args in self._ops:
            if op == "incr":
                key = args[0]
                raw = self._redis._data.get(key, b"0")
                val = int(raw) + 1
                self._redis._data[key] = str(val).encode()
                results.append(val)
            elif op == "expire":
                results.append(True)
        return results


@pytest.fixture()
def fake_redis() -> _InMemoryRedis:
    return _InMemoryRedis()


@pytest.fixture(autouse=True)
def _inject_store(fake_redis: _InMemoryRedis, monkeypatch: pytest.MonkeyPatch):  # pyright: ignore[reportUnusedFunction]
    """Replace the module-level store with our in-memory backend."""
    test_store = storage._EncryptedRedis(fake_redis, fernet=None)  # type: ignore[arg-type]
    monkeypatch.setattr(storage, "_store", test_store)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_code_data(**overrides) -> AuthCodeData:
    defaults = {
        "code": "test-code-123",
        "user_id": uuid.uuid4(),
        "email": "user@example.com",
        "organization_id": uuid.uuid4(),
        "is_platform_superuser": False,
        "client_id": "tracecat-mcp-oidc-internal",
        "redirect_uri": "https://mcp.example.com/auth/callback",
        "code_challenge": "challenge-value",
        "code_challenge_method": "S256",
        "scope": "openid profile email",
        "resource": "https://mcp.example.com/mcp",
        "nonce": None,
        "created_at": time.time(),
        "bound_ip": "abc123hash",
    }
    return AuthCodeData(**(defaults | overrides))


def _make_resume_transaction(**overrides) -> ResumeTransaction:
    defaults = {
        "transaction_id": "txn-abc-123",
        "authorize_params": {
            "response_type": "code",
            "client_id": "tracecat-mcp-oidc-internal",
            "redirect_uri": "https://mcp.example.com/auth/callback",
            "code_challenge": "challenge-value",
            "state": "random-state",
            "resource": "https://mcp.example.com/mcp",
        },
        "created_at": time.time(),
        "bound_ip": "def456hash",
    }
    return ResumeTransaction(**(defaults | overrides))


# ---------------------------------------------------------------------------
# Auth code tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_store_and_load_auth_code_roundtrip() -> None:
    data = _make_auth_code_data()
    await storage.store_auth_code(data)

    loaded = await storage.load_and_delete_auth_code(data.code)

    assert loaded is not None
    assert loaded.code == data.code
    assert loaded.user_id == data.user_id
    assert loaded.email == data.email
    assert loaded.organization_id == data.organization_id
    assert loaded.is_platform_superuser == data.is_platform_superuser
    assert loaded.scope == data.scope
    assert loaded.resource == data.resource


@pytest.mark.anyio
async def test_load_and_delete_auth_code_is_one_time_use() -> None:
    data = _make_auth_code_data()
    await storage.store_auth_code(data)

    first = await storage.load_and_delete_auth_code(data.code)
    second = await storage.load_and_delete_auth_code(data.code)

    assert first is not None
    assert second is None


@pytest.mark.anyio
async def test_load_and_delete_auth_code_returns_none_for_unknown() -> None:
    result = await storage.load_and_delete_auth_code("nonexistent-code")
    assert result is None


# ---------------------------------------------------------------------------
# Resume transaction tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_store_and_load_resume_transaction_roundtrip() -> None:
    txn = _make_resume_transaction()
    await storage.store_resume_transaction(txn)

    loaded = await storage.load_and_delete_resume_transaction(txn.transaction_id)

    assert loaded is not None
    assert loaded.transaction_id == txn.transaction_id
    assert loaded.authorize_params == txn.authorize_params
    assert loaded.bound_ip == txn.bound_ip


@pytest.mark.anyio
async def test_load_and_delete_resume_transaction_is_one_time_use() -> None:
    txn = _make_resume_transaction()
    await storage.store_resume_transaction(txn)

    first = await storage.load_and_delete_resume_transaction(txn.transaction_id)
    second = await storage.load_and_delete_resume_transaction(txn.transaction_id)

    assert first is not None
    assert second is None


# ---------------------------------------------------------------------------
# JTI tracking
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_store_jti(fake_redis: _InMemoryRedis) -> None:
    await storage.store_jti("jti-abc")

    raw = fake_redis.raw_get("mcp-oidc:jti:jti-abc")
    assert raw is not None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_token_rate_limit_allows_under_limit() -> None:
    result = await storage.check_token_rate_limit("1.2.3.4")
    assert result is True


@pytest.mark.anyio
async def test_check_token_rate_limit_blocks_over_limit() -> None:
    ip = "10.0.0.1"
    results = []
    for _ in range(11):
        results.append(await storage.check_token_rate_limit(ip))

    # First 10 should be allowed (TOKEN_RATE_LIMIT_PER_MINUTE = 10)
    assert all(results[:10])
    assert results[10] is False


# ---------------------------------------------------------------------------
# _EncryptedRedis encryption tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_encrypted_redis_with_fernet() -> None:
    """Roundtrip with Fernet encryption; raw value must differ from plaintext."""
    redis = _InMemoryRedis()
    fernet = Fernet(Fernet.generate_key())
    encrypted_store = storage._EncryptedRedis(redis, fernet=fernet)  # type: ignore[arg-type]

    plaintext = b"secret-data"
    await encrypted_store.set("key", plaintext, ttl=60)

    # Raw Redis value should be encrypted (not equal to plaintext).
    raw = redis.raw_get("key")
    assert raw is not None
    assert raw != plaintext

    # Decrypted value should match original.
    decrypted = await encrypted_store.get("key")
    assert decrypted == plaintext


@pytest.mark.anyio
async def test_encrypted_redis_without_fernet() -> None:
    """Without Fernet, raw Redis value equals plaintext."""
    redis = _InMemoryRedis()
    store = storage._EncryptedRedis(redis, fernet=None)  # type: ignore[arg-type]

    plaintext = b"plain-data"
    await store.set("key", plaintext, ttl=60)

    raw = redis.raw_get("key")
    assert raw == plaintext

    decrypted = await store.get("key")
    assert decrypted == plaintext
