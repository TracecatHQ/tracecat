from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.audit.service import AuditWebhookConfig
from tracecat.auth.types import Role


class FakeAuditWebhookClient:
    status_code = status.HTTP_200_OK
    calls: list[dict[str, Any]] = []

    def __init__(self, *, timeout: float, verify: bool) -> None:
        self.timeout = timeout
        self.verify = verify

    async def __aenter__(self) -> FakeAuditWebhookClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": self.timeout,
                "verify": self.verify,
            }
        )
        return httpx.Response(self.status_code)


class HangingAuditWebhookClient(FakeAuditWebhookClient):
    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": self.timeout,
                "verify": self.verify,
            }
        )
        await asyncio.Event().wait()
        raise AssertionError("wall-clock timeout should cancel the request")


@pytest.fixture(autouse=True)
def reset_fake_client() -> None:
    FakeAuditWebhookClient.status_code = status.HTTP_200_OK
    FakeAuditWebhookClient.calls = []
    HangingAuditWebhookClient.calls = []


@pytest.fixture
def sink_config() -> AuditWebhookConfig:
    return AuditWebhookConfig(
        webhook_url="https://audit.example.test/ingest",
        custom_headers={
            "X-Custom-Audit": "secret-value",
            "x-tracecat-test": "false",
        },
        custom_payload={"customer_field": "customer-value"},
        verify_ssl=False,
        payload_attribute="event",
    )


@pytest.mark.anyio
async def test_org_audit_webhook_test_posts_marked_event(
    client: TestClient,
    test_admin_role: Role,
    sink_config: AuditWebhookConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_config = AsyncMock(return_value=sink_config)
    monkeypatch.setattr(
        "tracecat.audit.test_fire.resolve_audit_sink_config", resolve_config
    )
    monkeypatch.setattr(
        "tracecat.audit.test_fire.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "ok": True,
        "receiver_status_code": status.HTTP_200_OK,
        "error_category": None,
    }
    resolve_config.assert_awaited_once()
    resolve_call = resolve_config.await_args
    assert resolve_call is not None
    assert resolve_call.kwargs["sink"] == "organization"
    assert resolve_call.kwargs["organization_id"] == test_admin_role.organization_id
    assert resolve_call.kwargs["session"] is not None
    assert len(FakeAuditWebhookClient.calls) == 1
    call = FakeAuditWebhookClient.calls[0]
    assert call["url"] == "https://audit.example.test/ingest"
    assert call["verify"] is False
    assert call["timeout"] == 5.0
    assert call["headers"]["X-Custom-Audit"] == "secret-value"
    assert "x-tracecat-test" not in call["headers"]
    assert call["headers"]["X-Tracecat-Test"] == "true"
    assert "X-Tracecat-Event-Id" in call["headers"]
    assert "X-Tracecat-Timestamp" in call["headers"]
    event = call["json"]["event"]
    assert event["organization_id"] == str(test_admin_role.organization_id)
    assert event["actor_id"] == str(test_admin_role.user_id)
    assert event["resource_type"] == "organization_setting"
    assert event["resource_id"] is None
    assert event["action"] == "connect"
    assert event["data"] == {"test": True}
    assert event["customer_field"] == "customer-value"


@pytest.mark.anyio
async def test_org_audit_webhook_test_surfaces_receiver_error(
    client: TestClient,
    test_admin_role: Role,
    sink_config: AuditWebhookConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAuditWebhookClient.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    monkeypatch.setattr(
        "tracecat.audit.test_fire.resolve_audit_sink_config",
        AsyncMock(return_value=sink_config),
    )
    monkeypatch.setattr(
        "tracecat.audit.test_fire.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "ok": False,
        "receiver_status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "error_category": "receiver_error",
    }


@pytest.mark.anyio
async def test_org_audit_webhook_test_enforces_wall_clock_timeout(
    client: TestClient,
    test_admin_role: Role,
    sink_config: AuditWebhookConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.test_fire._AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS", 0.01
    )
    monkeypatch.setattr(
        "tracecat.audit.test_fire.resolve_audit_sink_config",
        AsyncMock(return_value=sink_config),
    )
    monkeypatch.setattr(
        "tracecat.audit.test_fire.httpx.AsyncClient", HangingAuditWebhookClient
    )

    response = client.post("/settings/audit/test")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "ok": False,
        "receiver_status_code": None,
        "error_category": "timeout",
    }
    assert len(HangingAuditWebhookClient.calls) == 1
    assert HangingAuditWebhookClient.calls[0]["headers"]["X-Tracecat-Test"] == "true"


@pytest.mark.anyio
async def test_org_audit_webhook_test_returns_400_when_unconfigured(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.test_fire.resolve_audit_sink_config",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "tracecat.audit.test_fire.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Audit webhook is not configured"}
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
async def test_platform_audit_webhook_test_posts_platform_event(
    client: TestClient,
    test_admin_role: Role,
    sink_config: AuditWebhookConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_config = AsyncMock(return_value=sink_config)
    monkeypatch.setattr(
        "tracecat.audit.test_fire.resolve_audit_sink_config", resolve_config
    )
    monkeypatch.setattr(
        "tracecat.audit.test_fire.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/admin/settings/audit/test")

    assert response.status_code == status.HTTP_200_OK
    resolve_config.assert_awaited_once()
    resolve_call = resolve_config.await_args
    assert resolve_call is not None
    assert resolve_call.kwargs["sink"] == "platform"
    assert resolve_call.kwargs["organization_id"] is None
    assert resolve_call.kwargs["session"] is not None
    assert len(FakeAuditWebhookClient.calls) == 1
    event = FakeAuditWebhookClient.calls[0]["json"]["event"]
    assert event["organization_id"] is None
    assert event["actor_id"] == str(test_admin_role.user_id)
    assert event["resource_type"] == "platform_setting"
    assert event["resource_id"] is None
    assert event["data"] == {"test": True}
