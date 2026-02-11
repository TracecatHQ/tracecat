"""Tests for tracecat/webhooks/router.py.

Tests the webhook router's _incoming_webhook function directly, covering
standard trigger flow, echo response, vendor verification, and NDJSON batch.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, Response

from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.webhooks.router import _incoming_webhook

TEST_WF_ID = WorkflowUUID(int=100)


def _make_mock_defn(
    content: dict[str, Any] | None = None,
    registry_lock: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock ValidWorkflowDefinitionDep."""
    defn = MagicMock()
    defn.content = content or {
        "title": "Test Workflow",
        "description": "Test",
        "entrypoint": {"ref": "action_a"},
        "actions": [
            {
                "ref": "action_a",
                "action": "core.transform.reshape",
                "args": {"value": "test"},
            }
        ],
    }
    defn.registry_lock = registry_lock
    return defn


def _make_mock_request(
    headers: dict[str, str] | None = None,
    json_data: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock FastAPI Request."""
    req = MagicMock(spec=Request)
    req.headers = headers or {}
    req.json = AsyncMock(return_value=json_data or {})
    return req


@pytest.mark.anyio
class TestWebhookPost:
    """Test standard webhook trigger flow."""

    async def test_standard_webhook_trigger(self) -> None:
        """Standard POST should create workflow execution and return response."""
        mock_svc = AsyncMock()
        mock_svc.create_workflow_execution_wait_for_start.return_value = {
            "message": "Workflow execution created",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            new_callable=AsyncMock,
            return_value=mock_svc,
        ):
            result = await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload={"data": "test_payload"},
                echo=False,
                empty_echo=False,
                vendor=None,
                request=_make_mock_request(),
                content_type="application/json",
            )

        assert result is not None
        mock_svc.create_workflow_execution_wait_for_start.assert_awaited_once()

    async def test_webhook_with_none_payload(self) -> None:
        """Webhook with None payload (empty body) should still trigger."""
        mock_svc = AsyncMock()
        mock_svc.create_workflow_execution_wait_for_start.return_value = {
            "message": "ok",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            new_callable=AsyncMock,
            return_value=mock_svc,
        ):
            result = await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload=None,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=_make_mock_request(),
                content_type="application/json",
            )

        assert result is not None


@pytest.mark.anyio
class TestWebhookEcho:
    """Test echo response behavior."""

    async def test_echo_response_includes_payload(self) -> None:
        """Echo=True should include payload in response."""
        mock_svc = AsyncMock()
        mock_response_data = {
            "message": "ok",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }
        mock_svc.create_workflow_execution_wait_for_start.return_value = (
            mock_response_data
        )

        request_payload = {"echo_data": "hello"}
        mock_request = _make_mock_request(json_data=request_payload)

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            new_callable=AsyncMock,
            return_value=mock_svc,
        ):
            result = await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload=request_payload,
                echo=True,
                empty_echo=False,
                vendor=None,
                request=mock_request,
                content_type="application/json",
            )

        # Echo adds a "payload" key to the response dict
        result_dict = cast(dict[str, Any], result)
        assert result_dict["payload"] == request_payload

    async def test_empty_echo_returns_empty_response(self) -> None:
        """empty_echo=True should return empty 200 response."""
        mock_svc = AsyncMock()
        mock_svc.create_workflow_execution_wait_for_start.return_value = {
            "message": "ok",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            new_callable=AsyncMock,
            return_value=mock_svc,
        ):
            result = await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload=None,
                echo=True,
                empty_echo=True,
                vendor=None,
                request=_make_mock_request(),
                content_type="application/json",
            )

        response = cast(Response, result)
        assert response.status_code == 200


@pytest.mark.anyio
class TestWebhookVendorVerification:
    """Test vendor-specific webhook verification."""

    async def test_okta_verification_challenge(self) -> None:
        """Okta vendor with verification challenge should return challenge."""
        mock_svc = AsyncMock()
        mock_svc.create_workflow_execution_wait_for_start.return_value = {
            "message": "ok",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }

        mock_request = _make_mock_request(
            headers={"x-okta-verification-challenge": "challenge_token_123"}
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            new_callable=AsyncMock,
            return_value=mock_svc,
        ):
            result = await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload=None,
                echo=False,
                empty_echo=False,
                vendor="okta",
                request=mock_request,
                content_type="application/json",
            )

        result_dict = cast(dict[str, Any], result)
        assert result_dict["verification"] == "challenge_token_123"

    async def test_unsupported_vendor_raises(self) -> None:
        """Unsupported vendor should raise HTTPException."""
        mock_svc = AsyncMock()
        mock_svc.create_workflow_execution_wait_for_start.return_value = {
            "message": "ok",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                new_callable=AsyncMock,
                return_value=mock_svc,
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload=None,
                echo=False,
                empty_echo=False,
                vendor="unsupported_vendor",
                request=_make_mock_request(),
                content_type="application/json",
            )

        assert exc_info.value.status_code == 400
        assert "Unsupported vendor" in str(exc_info.value.detail)


@pytest.mark.anyio
class TestWebhookNDJSON:
    """Test NDJSON batch processing."""

    async def test_ndjson_batch_processing(self) -> None:
        """NDJSON content type with list payload should batch requests."""
        mock_svc = AsyncMock()
        mock_svc.create_workflow_execution_wait_for_start.return_value = {
            "message": "ok",
            "wf_id": str(TEST_WF_ID),
            "wf_exec_id": "wf-123/exec:abc",
        }

        ndjson_payload = [{"line": 1}, {"line": 2}, {"line": 3}]

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            new_callable=AsyncMock,
            return_value=mock_svc,
        ):
            result = await _incoming_webhook(
                workflow_id=TEST_WF_ID,
                defn=_make_mock_defn(),
                payload=ndjson_payload,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=_make_mock_request(),
                content_type="application/x-ndjson",
            )

        assert result is not None
        # Should have called create_workflow_execution_wait_for_start at least once
        mock_svc.create_workflow_execution_wait_for_start.assert_awaited()
