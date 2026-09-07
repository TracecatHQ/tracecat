import hashlib
import urllib.parse
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from fastapi import HTTPException, Request
from sqlalchemy.exc import NoResultFound

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.models import Webhook, WorkflowDefinition
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.webhooks.dependencies import (
    _ip_allowed,
    parse_webhook_payload,
    validate_incoming_webhook,
)
from tracecat.webhooks.router import _incoming_webhook
from tracecat.webhooks.schemas import WebhookApiKeyRead, _normalize_cidrs


class TestParseWebhookPayload:
    """Test cases for parse_webhook_payload function."""

    @staticmethod
    def _request_with_body(
        body: bytes,
        headers: dict[str, str] | None = None,
        *,
        chunk_size: int | None = None,
        content_type: str | None = None,
    ) -> Request:
        """Build a real Starlette request whose stream yields the given body.

        The body is cached the same way Starlette's ``body()`` caches it, so
        later ``form()`` or ``body()`` calls see the bytes without re-reading
        a consumed stream. ``content_type`` sets the scope header because
        Starlette's ``form()`` parses according to its own content-type
        header, not the one passed to the dependency.
        """
        merged_headers = dict(headers or {})
        if content_type is not None:
            merged_headers.setdefault("content-type", content_type)
        encoded_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in merged_headers.items()
        ]
        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "headers": encoded_headers,
            "query_string": b"",
        }
        if chunk_size is None:
            chunks = [body] if body else []
        else:
            chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]

        async def receive() -> dict[str, Any]:
            if chunks:
                return {
                    "type": "http.request",
                    "body": chunks.pop(0),
                    "more_body": bool(chunks),
                }
            return {"type": "http.disconnect"}

        request = Request(scope, receive)
        request._body = body  # noqa: SLF001
        return request

    @pytest.mark.anyio
    async def test_empty_body_returns_none(self):
        """Test that empty body returns None."""
        request = self._request_with_body(b"")

        result = await parse_webhook_payload(request, "application/json")
        assert result is None

    @pytest.mark.anyio
    async def test_json_content_type(self):
        """Test parsing JSON content type."""
        test_data = {"key": "value", "number": 42}
        request = self._request_with_body(orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "application/json")
        assert result == test_data

    @pytest.mark.anyio
    async def test_json_content_type_with_charset(self):
        """Test parsing JSON content type with charset parameter."""
        test_data = {"key": "value", "number": 42}
        request = self._request_with_body(orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "application/json; charset=utf-8")
        assert result == test_data

    @pytest.mark.anyio
    async def test_ndjson_content_type(self):
        """Test parsing NDJSON content type."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = self._request_with_body(ndjson_body)

        result = await parse_webhook_payload(request, "application/x-ndjson")
        assert result == test_data

    @pytest.mark.anyio
    async def test_ndjson_content_type_with_charset(self):
        """Test parsing NDJSON content type with charset parameter."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = self._request_with_body(ndjson_body)

        result = await parse_webhook_payload(
            request, "application/x-ndjson; charset=utf-8"
        )
        assert result == test_data

    @pytest.mark.anyio
    async def test_jsonlines_content_type(self):
        """Test parsing jsonlines content type."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = self._request_with_body(ndjson_body)

        result = await parse_webhook_payload(request, "application/jsonlines")
        assert result == test_data

    @pytest.mark.anyio
    async def test_jsonl_content_type_with_charset(self):
        """Test parsing jsonl content type with charset parameter."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = self._request_with_body(ndjson_body)

        result = await parse_webhook_payload(
            request, "application/jsonl; charset=utf-8"
        )
        assert result == test_data

    @pytest.mark.anyio
    async def test_form_urlencoded_content_type(self):
        """Test parsing form-urlencoded content type."""
        request = self._request_with_body(
            b"key=value&number=42",
            content_type="application/x-www-form-urlencoded",
        )

        result = await parse_webhook_payload(
            request, "application/x-www-form-urlencoded"
        )
        assert result == {"key": "value", "number": "42"}

    @pytest.mark.anyio
    async def test_form_urlencoded_content_type_with_charset(self):
        """Test parsing form-urlencoded content type with charset parameter."""
        request = self._request_with_body(
            b"key=value&number=42",
            content_type="application/x-www-form-urlencoded; charset=utf-8",
        )

        result = await parse_webhook_payload(
            request, "application/x-www-form-urlencoded; charset=utf-8"
        )
        assert result == {"key": "value", "number": "42"}

    @pytest.mark.anyio
    async def test_case_insensitive_content_type(self):
        """Test that content type matching is case insensitive."""
        test_data = {"key": "value"}
        request = self._request_with_body(orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "APPLICATION/JSON; CHARSET=UTF-8")
        assert result == test_data

    @pytest.mark.anyio
    async def test_content_type_with_whitespace(self):
        """Test that content type handles extra whitespace."""
        test_data = {"key": "value"}
        request = self._request_with_body(orjson.dumps(test_data))

        result = await parse_webhook_payload(
            request, "  application/json ; charset=utf-8  "
        )
        assert result == test_data

    @pytest.mark.anyio
    async def test_none_content_type_defaults_to_json(self):
        """Test that None content type defaults to JSON parsing."""
        test_data = {"key": "value"}
        request = self._request_with_body(orjson.dumps(test_data))

        result = await parse_webhook_payload(request, None)
        assert result == test_data

    @pytest.mark.anyio
    async def test_unknown_content_type_defaults_to_json(self):
        """Test that unknown content type defaults to JSON parsing."""
        test_data = {"key": "value"}
        request = self._request_with_body(orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "text/plain")
        assert result == test_data


class TestWebhookBodySizeCap:
    """Tests for the webhook request body size cap in parse_webhook_payload."""

    @pytest.fixture(autouse=True)
    def small_cap(self, monkeypatch: pytest.MonkeyPatch):
        """Use a small, deterministic cap for size tests."""
        monkeypatch.setattr(config, "TRACECAT__WEBHOOK_MAX_BODY_BYTES", 64)
        return 64

    @pytest.mark.anyio
    async def test_body_at_cap_is_accepted(self, small_cap: int):
        """A body exactly at the cap parses normally."""
        assert small_cap == 64
        # {"key":"<54 x's>"} is exactly 64 bytes.
        test_data = {"key": "x" * 54}
        body = orjson.dumps(test_data)
        assert len(body) == small_cap
        request = TestParseWebhookPayload._request_with_body(body)

        result = await parse_webhook_payload(request, "application/json")
        assert result == test_data

    @pytest.mark.anyio
    async def test_oversized_content_length_rejected_without_reading_body(
        self, small_cap: int
    ):
        """An over-cap content-length is rejected before consuming the stream."""
        assert small_cap == 64
        received: list[bytes] = []

        async def receive() -> dict[str, Any]:
            chunk = b"x" * 128
            received.append(chunk)
            return {"type": "http.request", "body": chunk, "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-length", b"4096")],
            "query_string": b"",
        }
        request = Request(scope, receive)

        with pytest.raises(HTTPException) as exc_info:
            await parse_webhook_payload(request, "application/json")
        assert exc_info.value.status_code == 413
        # The 413 fast path must not have pulled bytes from the stream.
        assert received == []

    @pytest.mark.anyio
    async def test_oversize_detected_during_streaming_read(self, small_cap: int):
        """A chunked body that grows past the cap is rejected mid-stream."""
        assert small_cap == 64
        request = TestParseWebhookPayload._request_with_body(b"x" * 65, chunk_size=16)

        with pytest.raises(HTTPException) as exc_info:
            await parse_webhook_payload(request, "application/json")
        assert exc_info.value.status_code == 413

    @pytest.mark.anyio
    async def test_ndjson_over_cap_rejected(self, small_cap: int):
        """The cap applies to NDJSON payloads too."""
        assert small_cap == 64
        # Six 11-byte lines plus 5 separator bytes is 71 bytes, above the cap.
        body = b"\n".join(b'{"line": %d}' % i for i in range(1, 7))
        assert len(body) == 71
        assert len(body) > small_cap
        request = TestParseWebhookPayload._request_with_body(body)

        with pytest.raises(HTTPException) as exc_info:
            await parse_webhook_payload(request, "application/x-ndjson")
        assert exc_info.value.status_code == 413

    @pytest.mark.anyio
    async def test_form_urlencoded_over_cap_rejected(self, small_cap: int):
        """The cap applies to form-urlencoded payloads too."""
        assert small_cap == 64
        body = ("key=" + "x" * 100).encode()
        request = TestParseWebhookPayload._request_with_body(body)

        with pytest.raises(HTTPException) as exc_info:
            await parse_webhook_payload(request, "application/x-www-form-urlencoded")
        assert exc_info.value.status_code == 413

    @pytest.mark.anyio
    async def test_form_urlencoded_under_cap_parses(self, small_cap: int):
        """Form parsing still works under the cap without a form() mock."""
        assert small_cap == 64
        request = TestParseWebhookPayload._request_with_body(
            b"key=value&number=42",
            content_type="application/x-www-form-urlencoded",
        )

        result = await parse_webhook_payload(
            request, "application/x-www-form-urlencoded"
        )
        assert result == {"key": "value", "number": "42"}

    @pytest.mark.anyio
    async def test_slack_interaction_payload_under_cap_parses(self, small_cap: int):
        """Slack interaction form posts still parse under the cap."""
        assert small_cap == 64
        slack_payload = orjson.dumps({"type": "block_actions"})
        body = urllib.parse.urlencode({"payload": slack_payload.decode()}).encode()
        assert 0 < len(body) <= small_cap
        request = TestParseWebhookPayload._request_with_body(
            body, content_type="application/x-www-form-urlencoded"
        )

        result = await parse_webhook_payload(
            request, "application/x-www-form-urlencoded"
        )
        assert result is not None
        assert result["payload"] == slack_payload.decode()


class TestWebhookNetworkHelpers:
    def test_normalize_cidrs(self):
        cidrs = ["192.168.1.0/24", "192.168.1.0/24", "10.0.0.1", "10.0.0.1/32"]
        normalized = _normalize_cidrs(cidrs)
        assert normalized == ["192.168.1.0/24", "10.0.0.1/32"]

    def test_ip_allowed_positive(self):
        assert _ip_allowed("192.168.1.5", ["192.168.1.0/24"]) is True

    def test_ip_allowed_negative(self):
        assert _ip_allowed("203.0.113.10", ["192.168.1.0/24"]) is False

    def test_normalize_cidrs_rejects_ipv6(self):
        with pytest.raises(ValueError, match="Only IPv4"):
            _normalize_cidrs(["2001:db8::/32"])


class TestWebhookApiKeyRead:
    def test_is_active_true_when_not_revoked(self):
        now = datetime.now(UTC)
        api_key = WebhookApiKeyRead(
            preview="tc_sk_abcd", created_at=now, revoked_at=None
        )
        assert api_key.is_active is True

    def test_is_active_false_when_revoked(self):
        now = datetime.now(UTC)
        api_key = WebhookApiKeyRead(
            preview="tc_sk_abcd", created_at=now, revoked_at=now
        )
        assert api_key.is_active is False


class TestValidateIncomingWebhook:
    @pytest.mark.anyio
    async def test_sets_role_with_workspace_organization_id(self):
        workflow_id = WorkflowUUID.new_uuid4()
        webhook_secret = "secret"
        workspace_id = uuid.uuid4()
        organization_id = uuid.uuid4()

        request = MagicMock(spec=Request)
        request.method = "POST"
        request.headers = {}
        request.client = None

        webhook = MagicMock(spec=Webhook)
        webhook.workflow_id = workflow_id
        webhook.secret = webhook_secret
        webhook.status = "online"
        webhook.normalized_methods = ["post"]
        webhook.allowlisted_cidrs = None
        webhook.api_key = None
        webhook.workspace_id = workspace_id

        workspace = MagicMock()
        workspace.organization_id = organization_id

        webhook_result = MagicMock()
        webhook_result.scalar_one.return_value = webhook
        workspace_result = MagicMock()
        workspace_result.scalar_one.return_value = workspace

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[webhook_result, workspace_result])

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_cm.__aexit__.return_value = None

        token = ctx_role.set(None)
        try:
            with patch(
                "tracecat.webhooks.dependencies.get_async_session_bypass_rls_context_manager",
                return_value=mock_session_cm,
            ):
                await validate_incoming_webhook(workflow_id, webhook_secret, request)

            role = ctx_role.get()
            assert role is not None
            assert role.workspace_id == workspace_id
            assert role.organization_id == organization_id
            assert role.service_id == "tracecat-runner"
        finally:
            ctx_role.reset(token)

    @pytest.mark.anyio
    async def test_returns_unauthorized_when_workspace_missing(self):
        workflow_id = WorkflowUUID.new_uuid4()
        webhook_secret = "secret"
        workspace_id = uuid.uuid4()

        request = MagicMock(spec=Request)
        request.method = "POST"
        request.headers = {}
        request.client = None

        webhook = MagicMock(spec=Webhook)
        webhook.workflow_id = workflow_id
        webhook.secret = webhook_secret
        webhook.status = "online"
        webhook.normalized_methods = ["post"]
        webhook.allowlisted_cidrs = None
        webhook.api_key = None
        webhook.workspace_id = workspace_id
        webhook.id = uuid.uuid4()

        webhook_result = MagicMock()
        webhook_result.scalar_one.return_value = webhook
        workspace_result = MagicMock()
        workspace_result.scalar_one.side_effect = NoResultFound()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[webhook_result, workspace_result])

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_cm.__aexit__.return_value = None

        token = ctx_role.set(None)
        try:
            with patch(
                "tracecat.webhooks.dependencies.get_async_session_bypass_rls_context_manager",
                return_value=mock_session_cm,
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await validate_incoming_webhook(
                        workflow_id, webhook_secret, request
                    )
        finally:
            ctx_role.reset(token)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized webhook request"


class TestIncomingWebhook:
    @staticmethod
    def _definition() -> WorkflowDefinition:
        return cast(
            WorkflowDefinition,
            SimpleNamespace(
                content={
                    "title": "Webhook test workflow",
                    "description": "Test workflow",
                    "entrypoint": {"ref": "start"},
                    "actions": [{"ref": "start", "action": "core.noop"}],
                    "config": {"enable_runtime_tests": False},
                },
                registry_lock=None,
            ),
        )

    @pytest.mark.anyio
    async def test_returns_success_only_after_start_acknowledged(self):
        workflow_id = WorkflowUUID.new("wf_4itKqkgCZrLhgYiq5L211X")
        payload = {"key": "value"}
        expected_response = {
            "message": "Workflow execution started",
            "wf_id": workflow_id,
            "wf_exec_id": f"{workflow_id.short()}/exec_123",
        }

        request = MagicMock(spec=Request)
        request.headers = {}
        request.json = AsyncMock(return_value=payload)

        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value=expected_response
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            response = await _incoming_webhook(
                workflow_id=workflow_id,
                defn=self._definition(),
                payload=payload,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/json",
            )

        assert response == expected_response
        mock_service.create_workflow_execution_wait_for_start.assert_awaited_once()

    @pytest.mark.anyio
    async def test_propagates_start_failure(self):
        workflow_id = WorkflowUUID.new("wf_4itKqkgCZrLhgYiq5L211X")
        payload = {"key": "value"}
        request = MagicMock(spec=Request)
        request.headers = {}

        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            side_effect=RuntimeError("start failed")
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            with pytest.raises(RuntimeError, match="start failed"):
                await _incoming_webhook(
                    workflow_id=workflow_id,
                    defn=self._definition(),
                    payload=payload,
                    echo=False,
                    empty_echo=False,
                    vendor=None,
                    request=request,
                    content_type="application/json",
                )


class TestWebhookSecret:
    """Test cases for Webhook.secret property.

    The webhook secret must use the legacy prefixed ID format (wh-{uuid.hex})
    to maintain backward compatibility with existing webhook URLs after the
    migration from prefixed string IDs to native UUIDs.
    """

    def test_secret_uses_legacy_prefixed_format(self):
        """Verify the secret is computed using 'wh-{uuid.hex}' format.

        This test ensures backward compatibility after migrating webhook IDs
        from prefixed strings (wh-abc123...) to native UUIDs. The secret hash
        must use the legacy format to prevent webhook URL changes.
        """

        # Known values for deterministic testing
        webhook_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        signing_secret = "test-signing-secret"

        # Expected: hash computed with legacy format "wh-{uuid.hex}"
        legacy_id = f"wh-{webhook_id.hex}"
        expected_secret = hashlib.sha256(
            f"{legacy_id}{signing_secret}".encode()
        ).hexdigest()

        # Create a mock webhook with the ID attribute
        webhook = MagicMock(spec=Webhook)
        webhook.id = webhook_id
        # Call the actual property implementation
        secret_getter = Webhook.secret.fget
        assert secret_getter is not None
        with patch.object(config, "TRACECAT__SIGNING_SECRET", signing_secret):
            actual_secret = secret_getter(webhook)

        assert actual_secret == expected_secret, (
            f"Webhook secret should use legacy 'wh-{{uuid.hex}}' format. "
            f"Expected hash of '{legacy_id}{signing_secret}', "
            f"got different hash. This would break existing webhook URLs."
        )

    def test_secret_not_using_raw_uuid_format(self):
        """Verify the secret is NOT computed using raw UUID string format.

        This is a regression test to catch if someone accidentally changes
        the secret computation to use the UUID directly without the 'wh-' prefix.
        """

        webhook_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        signing_secret = "test-signing-secret"

        # Wrong: hash computed with raw UUID string (would break existing URLs)
        wrong_secret = hashlib.sha256(
            f"{webhook_id}{signing_secret}".encode()
        ).hexdigest()

        # Create a mock webhook with the ID attribute
        webhook = MagicMock(spec=Webhook)
        webhook.id = webhook_id
        # Call the actual property implementation
        secret_getter = Webhook.secret.fget
        assert secret_getter is not None
        with patch.object(config, "TRACECAT__SIGNING_SECRET", signing_secret):
            actual_secret = secret_getter(webhook)

        assert actual_secret != wrong_secret, (
            "Webhook secret must NOT use raw UUID format. "
            "This would break backward compatibility with existing webhook URLs."
        )

    def test_secret_golden_value_from_v0_52_0(self):
        """Golden test with hardcoded value from v0.52.0.

        This test ensures webhook URLs remain stable across versions.
        The expected value was computed in v0.52.0 and must never change.

        DO NOT UPDATE THE EXPECTED VALUE - if this test fails, the webhook
        secret computation has changed and will break existing integrations.
        """
        webhook_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        signing_secret = "your-signing-secret"

        # Golden value computed in v0.52.0 - DO NOT CHANGE
        expected_secret = (
            "34ed3df2ab390f94c64106e35e170f59c08f4fa3cb6563831adfe2fa149938c3"
        )

        webhook = MagicMock(spec=Webhook)
        webhook.id = webhook_id
        secret_getter = Webhook.secret.fget
        assert secret_getter is not None
        with patch.object(config, "TRACECAT__SIGNING_SECRET", signing_secret):
            actual_secret = secret_getter(webhook)

        assert actual_secret == expected_secret, (
            f"Webhook secret has changed from v0.52.0 golden value. "
            f"Expected: {expected_secret}, Got: {actual_secret}. "
            "This will break existing webhook URLs!"
        )
