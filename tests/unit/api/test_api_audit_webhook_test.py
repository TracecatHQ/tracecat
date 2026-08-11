from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from inspect import signature
from typing import Any

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.settings import router as admin_settings_router

from tracecat import network
from tracecat.audit import service as audit_service_module
from tracecat.auth.types import Role
from tracecat.settings import router as settings_router
from tracecat.settings.schemas import AuditSettingsUpdate

_TEST_BODY: dict[str, Any] = {
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

    def __init__(self, *, timeout: float, verify: bool) -> None:
        self.timeout = timeout
        self.verify = verify

    async def __aenter__(self) -> FakeAuditWebhookClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[httpx.Response]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": self.timeout,
                "verify": self.verify,
            }
        )
        yield httpx.Response(self.status_code)


class HangingAuditWebhookClient(FakeAuditWebhookClient):
    @asynccontextmanager
    async def stream(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[httpx.Response]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": self.timeout,
                "verify": self.verify,
            }
        )
        await asyncio.Event().wait()
        raise AssertionError("wall-clock timeout should cancel the request")
        yield httpx.Response(self.status_code)


@pytest.fixture(autouse=True)
def reset_fake_client() -> None:
    FakeAuditWebhookClient.status_code = status.HTTP_200_OK
    FakeAuditWebhookClient.calls = []
    HangingAuditWebhookClient.calls = []


@pytest.fixture(autouse=True)
def allow_probe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every probe URL to a public address unless a test overrides it.

    The probe rejects URLs that do not resolve to public addresses, so the
    delivery-path tests must not depend on real DNS for their example hosts.
    """

    async def _noop(url: str, *, default_port: int) -> None:
        return None

    monkeypatch.setattr(
        audit_service_module, "validate_url_resolves_public_async", _noop
    )


def test_audit_webhook_test_routes_do_not_hold_request_sessions() -> None:
    assert "session" not in signature(settings_router.test_audit_webhook).parameters
    assert (
        "session" not in signature(admin_settings_router.test_audit_webhook).parameters
    )


@pytest.mark.anyio
async def test_org_audit_webhook_test_posts_marked_event_from_submitted_config(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test", json=_TEST_BODY)

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
async def test_org_audit_webhook_test_probes_submitted_not_saved_url(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must target exactly the submitted configuration."""
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    client.post("/settings/audit/test", json=_TEST_BODY)
    updated_body = {
        **_TEST_BODY,
        "audit_webhook_url": "https://audit.example.test/updated",
    }
    client.post("/settings/audit/test", json=updated_body)

    assert len(FakeAuditWebhookClient.calls) == 2
    assert (
        FakeAuditWebhookClient.calls[1]["url"] == "https://audit.example.test/updated"
    )


@pytest.mark.anyio
async def test_org_audit_webhook_test_surfaces_receiver_error(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAuditWebhookClient.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test", json=_TEST_BODY)

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service._AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS", 0.01
    )
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", HangingAuditWebhookClient
    )

    response = client.post("/settings/audit/test", json=_TEST_BODY)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "ok": False,
        "receiver_status_code": None,
        "error_category": "timeout",
    }
    assert len(HangingAuditWebhookClient.calls) == 1
    assert HangingAuditWebhookClient.calls[0]["headers"]["X-Tracecat-Test"] == "true"


@pytest.mark.anyio
async def test_org_audit_webhook_test_timeout_includes_dns_resolution(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _hang(url: str, *, default_port: int) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "tracecat.audit.service._AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS", 0.01
    )
    monkeypatch.setattr(
        audit_service_module, "validate_url_resolves_public_async", _hang
    )
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test", json=_TEST_BODY)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "ok": False,
        "receiver_status_code": None,
        "error_category": "timeout",
    }
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"audit_webhook_url": None},
        {"audit_webhook_url": "   "},
    ],
)
async def test_org_audit_webhook_test_returns_400_without_url(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test", json=body)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Audit webhook is not configured"}
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
async def test_probe_times_out_when_socket_budget_exhausted(
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe shares the delivery socket cap and honors the wall clock."""
    monkeypatch.setattr(
        "tracecat.audit.service._AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS", 0.05
    )
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )
    loop = asyncio.get_running_loop()
    audit_service_module._post_semaphores[loop] = asyncio.Semaphore(0)
    try:
        result = await audit_service_module.AuditService.probe_webhook(
            sink="organization",
            organization_id=test_admin_role.organization_id,
            role=test_admin_role,
            settings=AuditSettingsUpdate(
                audit_webhook_url="https://audit.example.test/ingest"
            ),
        )
    finally:
        audit_service_module._post_semaphores.pop(loop, None)

    assert result.ok is False
    assert result.error_category == "timeout"
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
async def test_probe_rejects_private_address_without_connecting(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL resolving to a private address is a 400 and never connects."""

    async def _reject(url: str, *, default_port: int) -> None:
        raise audit_service_module.DisallowedUrlError("Host is not allowed")

    monkeypatch.setattr(
        audit_service_module, "validate_url_resolves_public_async", _reject
    )
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/settings/audit/test", json=_TEST_BODY)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Audit webhook URL is not allowed"}
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
async def test_probe_url_guard_uses_real_resolver_for_loopback() -> None:
    """The shared guard rejects a loopback URL end to end."""
    with pytest.raises(network.DisallowedUrlError):
        await network.validate_url_resolves_public_async(
            "http://127.0.0.1:9000/ingest", default_port=443
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:99999/hook",
        "https://[::1/hook",
        # An over-long DNS label makes getaddrinfo raise UnicodeError, not
        # gaierror; the guard must still map it to a client error.
        f"https://{'a' * 64}.example.test/hook",
    ],
)
async def test_org_audit_webhook_test_returns_400_for_malformed_url(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setattr(
        audit_service_module,
        "validate_url_resolves_public_async",
        network.validate_url_resolves_public_async,
    )
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post(
        "/settings/audit/test", json={**_TEST_BODY, "audit_webhook_url": url}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Audit webhook URL is not allowed"}
    assert FakeAuditWebhookClient.calls == []


@pytest.mark.anyio
async def test_platform_audit_webhook_test_posts_platform_event(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.audit.service.httpx.AsyncClient", FakeAuditWebhookClient
    )

    response = client.post("/admin/settings/audit/test", json=_TEST_BODY)

    assert response.status_code == status.HTTP_200_OK
    assert len(FakeAuditWebhookClient.calls) == 1
    event = FakeAuditWebhookClient.calls[0]["json"]["event"]
    assert event["organization_id"] is None
    assert event["actor_id"] == str(test_admin_role.user_id)
    assert event["resource_type"] == "platform_setting"
    assert event["resource_id"] is None
    assert event["data"] == {"test": True}
