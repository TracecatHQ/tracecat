from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.contexts import ctx_log_masks, ctx_request_id, ctx_role
from tracecat.logger import security
from tracecat.middleware.request import RequestLoggingMiddleware


def test_sanitize_text_redacts_pii_secrets_and_urls() -> None:
    token = ctx_log_masks.set(("super-secret-value",))
    try:
        sanitized = security.sanitize_text(
            "email alice@example.com ip 203.0.113.42 token=abc123 "
            "Bearer my-token https://example.com/path?q=1 super-secret-value"
        )
    finally:
        ctx_log_masks.reset(token)

    assert sanitized is not None
    assert security.MASK_EMAIL in sanitized
    assert security.MASK_IP in sanitized
    assert "token=[REDACTED]" in sanitized
    assert "Bearer [REDACTED]" in sanitized
    assert "https://example.com" in sanitized
    assert "super-secret-value" not in sanitized


def test_sanitize_log_fields_flattens_context_and_hashes_identifiers(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        security.config, "TRACECAT__LOG_REDACTION_HMAC_KEY", "test-hmac-key"
    )
    security.resolve_log_redaction_hmac_key.cache_clear()

    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
    )
    role_token = ctx_role.set(role)
    request_token = ctx_request_id.set("req-123")
    try:
        sanitized = security.sanitize_log_fields(
            {
                "role": role,
                "email": "Alice@example.com",
                "params": {"email": "alice@example.com", "token": "secret-token"},
                "client_ip": "203.0.113.42",
                "session_id": "session-123",
            }
        )
    finally:
        ctx_request_id.reset(request_token)
        ctx_role.reset(role_token)
        security.resolve_log_redaction_hmac_key.cache_clear()

    assert sanitized["organization_id"] == str(role.organization_id)
    assert sanitized["workspace_id"] == str(role.workspace_id)
    assert sanitized["user_id"] == str(role.user_id)
    assert sanitized["request_id"] == "req-123"
    assert sanitized["session_id"] == "session-123"
    assert sanitized["email"] == security.MASK_TEXT
    assert sanitized["email_hash"].startswith(f"{security.LOG_HASH_VERSION}_")
    assert sanitized["params"] == {
        "type": "object",
        "item_count": 2,
        "keys": ["email", "token"],
        "truncated": False,
    }
    assert "client_ip" not in sanitized


def test_before_send_sentry_event_preserves_shape() -> None:
    event = {
        "user": {"email": "Alice@example.com"},
        "exception": {"values": [{"value": "Bearer secret-token"}]},
    }

    sanitized = security.before_send_sentry_event(event, {})

    assert isinstance(sanitized["user"]["email"], str)
    assert sanitized["user"]["email"] == security.MASK_TEXT
    assert sanitized["exception"]["values"][0]["value"] == "Bearer [REDACTED]"


def test_request_logging_middleware_logs_summary_only() -> None:
    app = FastAPI()
    app.state.logger = MagicMock()
    app.add_middleware(RequestLoggingMiddleware)
    seen_request_ids: list[str | None] = []

    async def create_item() -> dict[str, bool]:
        seen_request_ids.append(ctx_request_id.get())
        return {"ok": True}

    app.post("/items")(create_item)

    client = TestClient(app)
    body = '{"email":"alice@example.com"}'
    response = client.post(
        "/items?email=alice@example.com",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Forwarded-For": "203.0.113.42, 10.0.0.2",
            "X-Request-ID": "req-abc",
        },
        content=body,
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-abc"
    assert seen_request_ids == ["req-abc"]

    app.state.logger.info.assert_called_once()
    _, kwargs = app.state.logger.info.call_args
    assert kwargs["request_id"] == "req-abc"
    assert kwargs["query_param_count"] == 1
    assert kwargs["body_present"] is True
    assert kwargs["body_bytes"] == len(body)
    assert "params" not in kwargs
    assert "body" not in kwargs
    assert "client_ip" not in kwargs
