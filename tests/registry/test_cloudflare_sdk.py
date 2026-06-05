from types import SimpleNamespace
from typing import Any, cast

from tracecat_registry.integrations import cloudflare_sdk


def test_call_method_resolves_nested_resource_and_passes_params(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    def list_records(**params: Any) -> dict[str, Any]:
        calls["params"] = params
        return {"ok": True, "result": [{"id": "record-id"}]}

    client = SimpleNamespace(
        zones=SimpleNamespace(
            dns=SimpleNamespace(records=SimpleNamespace(list=list_records))
        )
    )

    def fake_cloudflare(*, api_token: str) -> SimpleNamespace:
        calls["api_token"] = api_token
        return client

    monkeypatch.setattr(
        cloudflare_sdk.secrets,
        "get",
        lambda key: "cf-token" if key == "CLOUDFLARE_API_TOKEN" else None,
    )
    monkeypatch.setattr(cloudflare_sdk, "Cloudflare", fake_cloudflare)

    result = cloudflare_sdk.call_method(
        resource="zones.dns.records",
        method_name="list",
        params={"zone_id": "zone-id"},
    )

    assert result == {"ok": True, "result": [{"id": "record-id"}]}
    assert calls == {
        "api_token": "cf-token",
        "params": {"zone_id": "zone-id"},
    }


def test_call_method_rejects_empty_resource_segment() -> None:
    try:
        cloudflare_sdk._resolve_resource(
            cast(Any, SimpleNamespace(zones=SimpleNamespace())), "zones..records"
        )
    except ValueError as e:
        assert "empty segments" in str(e)
    else:
        raise AssertionError("Expected empty resource segment to be rejected.")
