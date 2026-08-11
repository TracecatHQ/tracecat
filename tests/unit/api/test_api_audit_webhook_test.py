from __future__ import annotations

import asyncio
from collections.abc import Iterator
from inspect import signature
from typing import Any

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.settings import router as admin_settings_router

from tracecat.audit import service as audit_service_module
from tracecat.auth.types import Role
from tracecat.settings import router as settings_router

_SINK_SETTINGS: dict[str, Any] = {
    "audit_webhook_url": "https://audit.example.test/ingest",
    "audit_webhook_custom_headers": {
        "X-Custom-Audit": "secret-value",
        "x-tracecat-test": "false",
    },
    "audit_webhook_custom_payload": {"customer_field": "customer-value"},
    "audit_webhook_verify_ssl": False,
    "audit_webhook_payload_attribute": "event",
}


class FakeAuditWebhookClient:
    status_code = status.HTTP_200_OK
    calls: list[dict[str, Any]] = []
    settings_reads = 0
    settings_reads_active = 0

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
        assert self.settings_reads_active == 0
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
        assert self.settings_reads_active == 0
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
    FakeAuditWebhookClient.settings_reads = 0
    FakeAuditWebhookClient.settings_reads_active = 0
    HangingAuditWebhookClient.calls = []


@pytest.fixture(autouse=True)
def clear_audit_setting_cache() -> Iterator[None]:
    audit_service_module._get_audit_setting_cached.cache_clear()
    yield
    audit_service_module._get_audit_setting_cached.cache_clear()


@pytest.fixture
def sink_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Back self-managed cache misses without touching PostgreSQL."""
    settings = dict(_SINK_SETTINGS)

    async def read_setting(key: str, *, default: Any = None) -> Any | None:
        FakeAuditWebhookClient.settings_reads += 1
        FakeAuditWebhookClient.settings_reads_active += 1
        try:
            value = settings.get(key)
            return default if value is None and default is not None else value
        finally:
            FakeAuditWebhookClient.settings_reads_active -= 1

    async def fetch_platform_setting(key: str) -> Any | None:
        return await read_setting(key)

    async def get_setting(
        key: str, *, role: Any = None, session: Any = None, default: Any = None
    ) -> Any | None:
        assert session is None
        return await read_setting(key, default=default)

    monkeypatch.setattr(
        audit_service_module, "_fetch_platform_setting", fetch_platform_setting
    )
    monkeypatch.setattr("tracecat.settings.service.get_setting", get_setting)
    return settings


def test_audit_webhook_test_routes_do_not_hold_request_sessions() -> None:
    assert "session" not in signature(settings_router.test_audit_webhook).parameters
    assert (
        "session" not in signature(admin_settings_router.test_audit_webhook).parameters
    )


@pytest.mark.anyio
async def test_org_audit_webhook_test_posts_marked_event(
    client: TestClient,
    test_admin_role: Role,
    sink_settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "ok": True,
        "receiver_status_code": status.HTTP_200_OK,
        "error_category": None,
    }
    assert len(FakeAuditWebhookClient.calls) == 1
    call = FakeAuditWebhookClient.calls[0]
    assert call["url"] == "https://audit.example.test/ingest"
    assert call["verify"] is False
    assert call["timeout"] == 5.0
    assert call["headers"]["X-Custom-Audit"] == "secret-value"
    assert "x-tracecat-test" not in call["headers"]
    assert call["headers"]["X-Tracecat-Test"] == "true"
    event = call["json"]["event"]
    assert event["organization_id"] == str(test_admin_role.organization_id)
    assert event["actor_id"] == str(test_admin_role.user_id)
    assert event["resource_type"] == "organization_setting"
    assert event["resource_id"] is None
    assert event["action"] == "connect"
    assert event["data"] == {"test": True}
    assert event["customer_field"] == "customer-value"


@pytest.mark.anyio
async def test_org_audit_webhook_test_reads_fresh_settings(
    client: TestClient,
    test_admin_role: Role,
    sink_settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test fired right after a save must not post to the stale URL."""
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    client.post("/settings/audit/test")
    sink_settings["audit_webhook_url"] = "https://audit.example.test/updated"
    client.post("/settings/audit/test")

    assert len(FakeAuditWebhookClient.calls) == 2
    assert (
        FakeAuditWebhookClient.calls[1]["url"] == "https://audit.example.test/updated"
    )


@pytest.mark.anyio
async def test_org_audit_webhook_test_surfaces_receiver_error(
    client: TestClient,
    test_admin_role: Role,
    sink_settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAuditWebhookClient.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
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
    sink_settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service._AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS", 0.01
    )
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", HangingAuditWebhookClient
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
    sink_settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink_settings["audit_webhook_url"] = None
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Audit webhook is not configured"}
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
async def test_platform_audit_webhook_test_posts_platform_event(
    client: TestClient,
    test_admin_role: Role,
    sink_settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/admin/settings/audit/test")

    assert response.status_code == status.HTTP_200_OK
    assert len(FakeAuditWebhookClient.calls) == 1
    event = FakeAuditWebhookClient.calls[0]["json"]["event"]
    assert event["organization_id"] is None
    assert event["actor_id"] == str(test_admin_role.user_id)
    assert event["resource_type"] == "platform_setting"
    assert event["resource_id"] is None
    assert event["data"] == {"test": True}
