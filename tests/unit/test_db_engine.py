from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock

import pytest
from loguru import logger
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from tracecat import config
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import (
    _get_db_uri,
    get_async_auth_engine,
    get_async_engine,
    get_async_session,
    get_async_session_auth,
    get_async_session_bypass_rls,
    reset_async_engine,
)
from tracecat.db.exceptions import (
    AuthPoolExhaustedError,
    DatabasePoolAcquisitionOrderError,
)


class DummySecretsClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def get_secret_value(self, *, SecretId: str) -> dict[str, object]:
        return self._response


class DummySession:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def client(self, *, service_name: str) -> DummySecretsClient:
        assert service_name == "secretsmanager"
        return DummySecretsClient(self._response)


def test_get_db_uri_logs_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response: dict[str, object] = {"SecretBinary": base64.b64encode(b"\xff\xfe")}
    monkeypatch.setattr(config, "TRACECAT__DB_PASS__ARN", "arn:secret")
    monkeypatch.setattr(
        "tracecat.db.engine.boto3.session.Session",
        lambda: DummySession(response),
    )

    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(message.record["message"]),
        level="ERROR",
    )
    try:
        with pytest.raises(UnicodeDecodeError):
            _get_db_uri()
    finally:
        logger.remove(sink_id)

    assert any(
        "SecretBinary must be UTF-8 encoded text or JSON." in message
        for message in messages
    )


@pytest.mark.anyio
async def test_get_async_session_applies_role_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    set_from_role = AsyncMock()

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr("tracecat.db.engine.set_rls_context_from_role", set_from_role)
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", AsyncMock())
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.ENFORCE)
    monkeypatch.setattr("tracecat.db.engine.get_async_engine", lambda: object())

    generator = get_async_session()
    yielded = await anext(generator)
    await generator.aclose()

    assert yielded is session
    set_from_role.assert_awaited_once_with(session)


@pytest.mark.anyio
async def test_get_async_session_off_mode_sets_bypass_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    set_context = AsyncMock()
    set_from_role = AsyncMock()

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", set_context)
    monkeypatch.setattr("tracecat.db.engine.set_rls_context_from_role", set_from_role)
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.OFF)
    monkeypatch.setattr("tracecat.db.engine.get_async_engine", lambda: object())

    generator = get_async_session()
    yielded = await anext(generator)
    await generator.aclose()

    assert yielded is session
    set_context.assert_awaited_once_with(
        session,
        org_id=None,
        workspace_id=None,
        user_id=None,
        bypass=True,
    )
    set_from_role.assert_not_awaited()


@pytest.mark.anyio
async def test_get_async_session_shadow_mode_sets_bypass_context_with_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    set_context = AsyncMock()
    set_from_role = AsyncMock()
    role = Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", set_context)
    monkeypatch.setattr("tracecat.db.engine.set_rls_context_from_role", set_from_role)
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.SHADOW)
    monkeypatch.setattr("tracecat.db.engine.get_async_engine", lambda: object())

    token = ctx_role.set(role)
    try:
        generator = get_async_session()
        yielded = await anext(generator)
        await generator.aclose()
    finally:
        ctx_role.reset(token)

    assert yielded is session
    set_context.assert_awaited_once_with(
        session,
        org_id=None,
        workspace_id=None,
        user_id=role.user_id,
        bypass=True,
    )
    set_from_role.assert_not_awaited()


@pytest.mark.anyio
async def test_get_async_session_bypass_sets_explicit_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    set_context = AsyncMock()

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", set_context)
    monkeypatch.setattr("tracecat.db.engine.get_async_engine", lambda: object())

    generator = get_async_session_bypass_rls()
    yielded = await anext(generator)
    await generator.aclose()

    assert yielded is session
    set_context.assert_awaited_once_with(
        session,
        org_id=None,
        workspace_id=None,
        user_id=None,
        bypass=True,
    )


@pytest.mark.anyio
async def test_only_auth_session_binds_to_auth_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """General bypass work stays on main; only auth uses the bulkhead."""
    binds: list[AsyncEngine] = []

    def record_bind(bind: AsyncEngine, **kwargs: object) -> AsyncMock:
        binds.append(bind)
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = AsyncMock()
        session_cm.__aexit__.return_value = None
        return session_cm

    monkeypatch.setattr("tracecat.db.engine.AsyncSession", record_bind)
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", AsyncMock())
    monkeypatch.setattr("tracecat.db.engine.set_rls_context_from_role", AsyncMock())
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.OFF)

    request_generator = get_async_session()
    await anext(request_generator)
    await request_generator.aclose()

    bypass_generator = get_async_session_bypass_rls()
    await anext(bypass_generator)
    await bypass_generator.aclose()

    auth_generator = get_async_session_auth()
    await anext(auth_generator)
    await auth_generator.aclose()

    request_bind, bypass_bind, auth_bind = binds
    assert request_bind is get_async_engine()
    assert bypass_bind is get_async_engine()
    assert auth_bind is get_async_auth_engine()
    assert request_bind is not auth_bind
    assert request_bind.pool is not auth_bind.pool


@pytest.mark.anyio
async def test_auth_engine_pool_size_honors_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_POOL_SIZE", 11)
    monkeypatch.setattr(config, "TRACECAT__DB_AUTH_POOL_SIZE", 3)
    monkeypatch.setattr(config, "TRACECAT__DB_AUTH_MAX_OVERFLOW", 4)
    reset_async_engine()

    engine = get_async_engine()
    auth_engine = get_async_auth_engine()
    try:
        pool = engine.pool
        auth_pool = auth_engine.pool
        assert isinstance(pool, QueuePool)
        assert isinstance(auth_pool, QueuePool)
        assert auth_pool.size() == 3
        assert pool.size() == 11
    finally:
        await engine.dispose()
        await auth_engine.dispose()
        reset_async_engine()


@pytest.mark.anyio
async def test_main_session_acquisition_while_auth_session_held_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = AsyncMock()
    session_cm.__aexit__.return_value = None

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", AsyncMock())
    monkeypatch.setattr("tracecat.db.engine.get_async_auth_engine", lambda: object())

    auth_generator = get_async_session_auth()
    await anext(auth_generator)
    try:
        main_generator = get_async_session()
        with pytest.raises(DatabasePoolAcquisitionOrderError):
            await anext(main_generator)
    finally:
        await auth_generator.aclose()


@pytest.mark.anyio
async def test_nested_auth_session_acquisition_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = AsyncMock()
    session_cm.__aexit__.return_value = None

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr("tracecat.db.engine.set_rls_context", AsyncMock())
    monkeypatch.setattr("tracecat.db.engine.get_async_auth_engine", lambda: object())

    outer_generator = get_async_session_auth()
    await anext(outer_generator)
    try:
        nested_generator = get_async_session_auth()
        with pytest.raises(DatabasePoolAcquisitionOrderError):
            await anext(nested_generator)
    finally:
        await outer_generator.aclose()


@pytest.mark.anyio
async def test_auth_pool_timeout_raises_typed_exhaustion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = AsyncMock()
    session_cm.__aexit__.return_value = None

    monkeypatch.setattr(
        "tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm
    )
    monkeypatch.setattr(
        "tracecat.db.engine.set_rls_context",
        AsyncMock(side_effect=SQLAlchemyTimeoutError()),
    )
    monkeypatch.setattr("tracecat.db.engine.get_async_auth_engine", lambda: object())

    generator = get_async_session_auth()
    with pytest.raises(AuthPoolExhaustedError):
        await anext(generator)
