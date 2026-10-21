import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from fastapi import HTTPException, Request
from fastapi.datastructures import FormData
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
from tracecat.webhooks.schemas import _normalize_cidrs

_JSON_BODY = orjson.dumps({"key": "value", "number": 42})
_NDJSON_BODY = b'{"line": 1}\n{"line": 2}'
_NDJSON_EXPECTED = [{"line": 1}, {"line": 2}]
_FORM_BODY = b"key=value&number=42"
_FORM_EXPECTED = {"key": "value", "number": "42"}


class TestParseWebhookPayload:
    """Test cases for parse_webhook_payload function."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("content_type", "body", "expected"),
        [
            pytest.param("application/json", b"", None, id="empty_body"),
            pytest.param(
                "application/json",
                _JSON_BODY,
                {"key": "value", "number": 42},
                id="json",
            ),
            pytest.param(
                "application/json; charset=utf-8",
                _JSON_BODY,
                {"key": "value", "number": 42},
                id="json_charset",
            ),
            pytest.param(
                "application/x-ndjson", _NDJSON_BODY, _NDJSON_EXPECTED, id="ndjson"
            ),
            pytest.param(
                "application/x-ndjson; charset=utf-8",
                _NDJSON_BODY,
                _NDJSON_EXPECTED,
                id="ndjson_charset",
            ),
            pytest.param(
                "application/jsonlines",
                _NDJSON_BODY,
                _NDJSON_EXPECTED,
                id="jsonlines",
            ),
            pytest.param(
                "application/jsonl; charset=utf-8",
                _NDJSON_BODY,
                _NDJSON_EXPECTED,
                id="jsonl_charset",
            ),
            pytest.param(
                "application/x-www-form-urlencoded",
                _FORM_BODY,
                _FORM_EXPECTED,
                id="form_urlencoded",
            ),
            pytest.param(
                "application/x-www-form-urlencoded; charset=utf-8",
                _FORM_BODY,
                _FORM_EXPECTED,
                id="form_urlencoded_charset",
            ),
            pytest.param(
                "APPLICATION/JSON; CHARSET=UTF-8",
                _JSON_BODY,
                {"key": "value", "number": 42},
                id="case_insensitive",
            ),
            pytest.param(
                "  application/json ; charset=utf-8  ",
                _JSON_BODY,
                {"key": "value", "number": 42},
                id="whitespace",
            ),
            pytest.param(
                None, _JSON_BODY, {"key": "value", "number": 42}, id="none_is_json"
            ),
            pytest.param(
                "text/plain",
                _JSON_BODY,
                {"key": "value", "number": 42},
                id="unknown_is_json",
            ),
        ],
    )
    async def test_parse_webhook_payload(
        self, content_type: str | None, body: bytes, expected: object
    ):
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=body)
        request.form = AsyncMock(
            return_value=FormData([("key", "value"), ("number", "42")])
        )

        result = await parse_webhook_payload(request, content_type)
        assert result == expected


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

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("peer", "forwarded_for", "allowed"),
        [
            pytest.param(
                "10.244.3.17", "203.0.113.7", True, id="sender_behind_private_proxy"
            ),
            pytest.param(
                "10.244.3.17", "203.0.113.7, 10.0.2.234", True, id="two_proxy_hops"
            ),
            pytest.param(
                "10.244.3.17", "198.51.100.9", False, id="sender_not_allowlisted"
            ),
            pytest.param("10.244.3.17", None, False, id="proxy_peer_only"),
            pytest.param("203.0.113.7", None, True, id="direct_sender"),
            pytest.param(
                "198.51.100.9", "203.0.113.7", False, id="untrusted_peer_spoofs_xff"
            ),
        ],
    )
    async def test_allowlist_resolves_client_behind_trusted_proxies(
        self, peer: str, forwarded_for: str | None, allowed: bool
    ):
        workflow_id = WorkflowUUID.new_uuid4()
        webhook_secret = "secret"

        headers = (
            [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
        )
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "query_string": b"",
                "headers": headers,
                "client": (peer, 51234),
            }
        )

        webhook = MagicMock(spec=Webhook)
        webhook.id = uuid.uuid4()
        webhook.workflow_id = workflow_id
        webhook.secret = webhook_secret
        webhook.status = "online"
        webhook.normalized_methods = ["post"]
        webhook.allowlisted_cidrs = ["203.0.113.0/24"]
        webhook.api_key = None
        webhook.workspace_id = uuid.uuid4()

        workspace = MagicMock()
        workspace.organization_id = uuid.uuid4()

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
                if allowed:
                    await validate_incoming_webhook(
                        workflow_id, webhook_secret, request
                    )
                else:
                    with pytest.raises(HTTPException) as exc_info:
                        await validate_incoming_webhook(
                            workflow_id, webhook_secret, request
                        )
                    assert exc_info.value.status_code == 403
        finally:
            ctx_role.reset(token)


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


class TestWebhookSecret:
    """Test cases for Webhook.secret property.

    The webhook secret must use the legacy prefixed ID format (wh-{uuid.hex})
    to maintain backward compatibility with existing webhook URLs after the
    migration from prefixed string IDs to native UUIDs.
    """

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
