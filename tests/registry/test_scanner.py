from typing import Any

import httpx
import pytest
import respx
from tracecat_registry.integrations import scanner


def _fake_secret(key: str) -> str:
    secrets = {
        "SCANNER_API_KEY": "scanner-key",
        "SCANNER_BASE_URL": "https://api.example.scanner.dev/",
    }
    return secrets[key]


@pytest.mark.anyio
async def test_request_builds_scanner_auth_headers(monkeypatch) -> None:
    monkeypatch.setattr(scanner.secrets, "get", _fake_secret)

    with respx.mock:
        route = respx.get("https://api.example.scanner.dev/v1/index/idx_123").mock(
            return_value=httpx.Response(200, json={"id": "idx_123"})
        )
        result = await scanner.get_index(index_id="idx_123")

        assert result == {"id": "idx_123"}
        request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer scanner-key"
    assert request.headers["Accept"] == "application/json"


def test_resolve_base_url_from_required_secret(monkeypatch) -> None:
    monkeypatch.setattr(scanner.secrets, "get", lambda key: "api.example.scanner.dev/")

    assert scanner._resolve_base_url() == "https://api.example.scanner.dev"


def test_scanner_secret_requires_api_key_and_base_url() -> None:
    assert scanner.scanner_secret.keys == ["SCANNER_API_KEY", "SCANNER_BASE_URL"]
    assert scanner.scanner_secret.optional_keys is None


@pytest.mark.anyio
async def test_list_detection_rules_uses_scanner_pagination_params(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    async def fake_request(
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.update(method=method, path=path, params=params)
        return {"ok": True}

    monkeypatch.setattr(scanner, "_request", fake_request)

    result = await scanner.list_detection_rules(
        tenant_id="tenant-id",
        page_size=25,
        page_token="next-page",
    )

    assert result == {"ok": True}
    assert calls == {
        "method": "GET",
        "path": "/v1/detection_rule",
        "params": {
            "tenant_id": "tenant-id",
            "pagination[page_size]": 25,
            "pagination[page_token]": "next-page",
        },
    }


@pytest.mark.anyio
async def test_create_detection_rule_omits_none_but_keeps_false_and_empty_list(
    monkeypatch,
) -> None:
    calls: dict[str, Any] = {}

    async def fake_request(
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.update(method=method, path=path, payload=payload)
        return {"ok": True}

    monkeypatch.setattr(scanner, "_request", fake_request)

    result = await scanner.create_detection_rule(
        tenant_id="tenant-id",
        name="Errors",
        description="Detect repeated errors",
        time_range_s=300,
        run_frequency_s=300,
        enabled_state_override="Active",
        severity="Critical",
        query_text="error | count | where @q.count > 100",
        event_sink_ids=[],
        alert_per_row=False,
    )

    assert result == {"ok": True}
    assert calls["payload"] == {
        "tenant_id": "tenant-id",
        "name": "Errors",
        "description": "Detect repeated errors",
        "time_range_s": 300,
        "run_frequency_s": 300,
        "enabled_state_override": "Active",
        "severity": "Critical",
        "query_text": "error | count | where @q.count > 100",
        "event_sink_ids": [],
        "alert_per_row": False,
    }


@pytest.mark.anyio
async def test_validate_detection_rule_yaml_sends_raw_yaml(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    async def fake_request(
        method: str,
        path: str,
        *,
        content: str | bytes | None = None,
        content_type: str = "application/json",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.update(
            method=method,
            path=path,
            content=content,
            content_type=content_type,
        )
        return {"is_valid": True, "error": None}

    monkeypatch.setattr(scanner, "_request", fake_request)

    result = await scanner.validate_detection_rule_yaml(yaml_text="name: Test\n")

    assert result == {"is_valid": True, "error": None}
    assert calls == {
        "method": "POST",
        "path": "/v1/detection_rule_yaml/validate",
        "content": "name: Test\n",
        "content_type": "application/x-yaml",
    }
