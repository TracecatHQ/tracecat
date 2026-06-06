from types import SimpleNamespace
from typing import Any, cast

import pydantic
import pytest
from tracecat_registry.integrations import cloudflare_sdk


class FakeCloudflareModel(pydantic.BaseModel):
    """Cloudflare SDK responses are pydantic models; to_jsonable_python serializes them."""

    id: str


class FakeCloudflarePage:
    """Mimics a Cloudflare page: iterating it auto-paginates across all pages."""

    def __init__(self, pages: list[list[Any]]) -> None:
        self.pages = pages

    def __iter__(self) -> Any:
        for page in self.pages:
            yield from page


def test_call_method_resolves_nested_resource_and_passes_params(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    def list_records(**params: Any) -> dict[str, Any]:
        calls["params"] = params
        return {"ok": True, "result": [{"id": "record-id"}]}

    client = SimpleNamespace(
        dns=SimpleNamespace(records=SimpleNamespace(list=list_records))
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
        resource="dns.records",
        method_name="list",
        params={"zone_id": "zone-id"},
    )

    assert result == {"ok": True, "result": [{"id": "record-id"}]}
    assert calls == {
        "api_token": "cf-token",
        "params": {"zone_id": "zone-id"},
    }


def test_call_method_rejects_empty_resource_segment() -> None:
    with pytest.raises(ValueError, match="empty"):
        cloudflare_sdk._resolve_resource(
            cast(Any, SimpleNamespace(zones=SimpleNamespace())), "zones..records"
        )


@pytest.mark.parametrize(
    ("resource", "method_name", "message"),
    [
        ("zones._raw", "list", "Resource path segment"),
        ("zones", "_post", "Method name"),
        ("zones", "", "Method name"),
    ],
)
def test_call_method_rejects_private_or_empty_names(
    monkeypatch, resource: str, method_name: str, message: str
) -> None:
    monkeypatch.setattr(
        cloudflare_sdk.secrets,
        "get",
        lambda key: "cf-token" if key == "CLOUDFLARE_API_TOKEN" else None,
    )
    monkeypatch.setattr(
        cloudflare_sdk,
        "Cloudflare",
        lambda *, api_token: SimpleNamespace(
            zones=SimpleNamespace(_raw=SimpleNamespace(list=lambda: {}))
        ),
    )

    with pytest.raises(ValueError, match=message):
        cloudflare_sdk.call_method(
            resource=resource,
            method_name=method_name,
        )


def test_call_method_rejects_paginated_result(monkeypatch) -> None:
    monkeypatch.setattr(cloudflare_sdk, "BaseSyncPage", FakeCloudflarePage)
    monkeypatch.setattr(
        cloudflare_sdk.secrets,
        "get",
        lambda key: "cf-token" if key == "CLOUDFLARE_API_TOKEN" else None,
    )
    monkeypatch.setattr(
        cloudflare_sdk,
        "Cloudflare",
        lambda *, api_token: SimpleNamespace(
            zones=SimpleNamespace(
                list=lambda **_params: FakeCloudflarePage([[{"id": "1"}]])
            )
        ),
    )

    with pytest.raises(ValueError, match="call_paginated_method"):
        cloudflare_sdk.call_method(resource="zones", method_name="list")


def test_call_paginated_method_flattens_all_page_items(monkeypatch) -> None:
    calls: dict[str, Any] = {}
    page = FakeCloudflarePage(
        [
            [FakeCloudflareModel(id="one")],
            [FakeCloudflareModel(id="two"), {"id": "three"}],
        ]
    )

    def list_zones(**params: Any) -> FakeCloudflarePage:
        calls["params"] = params
        return page

    monkeypatch.setattr(cloudflare_sdk, "BaseSyncPage", FakeCloudflarePage)
    monkeypatch.setattr(
        cloudflare_sdk.secrets,
        "get",
        lambda key: "cf-token" if key == "CLOUDFLARE_API_TOKEN" else None,
    )
    monkeypatch.setattr(
        cloudflare_sdk,
        "Cloudflare",
        lambda *, api_token: SimpleNamespace(zones=SimpleNamespace(list=list_zones)),
    )

    result = cloudflare_sdk.call_paginated_method(
        resource="zones",
        method_name="list",
        params={"account_id": "account-id"},
    )

    assert result == [{"id": "one"}, {"id": "two"}, {"id": "three"}]
    assert calls == {"params": {"account_id": "account-id"}}
