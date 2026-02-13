from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest
from loguru import logger

from tracecat import config
from tracecat.db.engine import (
    _get_db_uri,
    get_async_session,
    get_async_session_bypass_rls,
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

    monkeypatch.setattr("tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm)
    monkeypatch.setattr("tracecat.db.engine.set_rls_context_from_role", set_from_role)
    monkeypatch.setattr("tracecat.db.engine.get_async_engine", lambda: object())

    generator = get_async_session()
    yielded = await anext(generator)
    await generator.aclose()

    assert yielded is session
    set_from_role.assert_awaited_once_with(session)


@pytest.mark.anyio
async def test_get_async_session_bypass_sets_explicit_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    set_context = AsyncMock()

    monkeypatch.setattr("tracecat.db.engine.AsyncSession", lambda *args, **kwargs: session_cm)
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
