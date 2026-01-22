from __future__ import annotations

import base64

import pytest
from loguru import logger

from tracecat import config
from tracecat.db.engine import _get_db_uri


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
