from types import SimpleNamespace
from typing import Any

import pytest
from tracecat_registry.integrations import azure_admin_sdk


class FakeAzureModel:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def as_dict(self) -> dict[str, Any]:
        return self.value


class FakeItemPaged:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def __iter__(self) -> Any:
        return iter(self.items)


def test_call_method_uses_oauth_token_and_dispatches_client_method(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class ResourceGroups:
        def list(self, **params: Any) -> FakeAzureModel:
            calls["params"] = params
            return FakeAzureModel({"value": [{"name": "rg-test"}]})

    class ResourceManagementClient:
        def __init__(
            self, *, credential: Any, subscription_id: str, **client_kwargs: Any
        ) -> None:
            calls["subscription_id"] = subscription_id
            calls["client_kwargs"] = client_kwargs
            calls["token"] = credential.get_token(
                "https://management.azure.com/.default"
            ).token
            self.resource_groups = ResourceGroups()

        def close(self) -> None:
            calls["closed"] = True

    def fake_import_module(module_name: str) -> SimpleNamespace:
        calls["module_name"] = module_name
        return SimpleNamespace(ResourceManagementClient=ResourceManagementClient)

    def fake_get(key: str) -> str:
        calls["secret_key"] = key
        return "azure-token"

    monkeypatch.setattr(azure_admin_sdk.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(azure_admin_sdk.secrets, "get", fake_get)

    result = azure_admin_sdk.call_method(
        module_name="azure.mgmt.resource",
        client_class="ResourceManagementClient",
        subscription_id="00000000-0000-0000-0000-000000000000",
        method_path="resource_groups",
        method_name="list",
        params={"top": 5},
        client_kwargs={"base_url": "https://management.azure.com"},
    )

    assert result == {"value": [{"name": "rg-test"}]}
    assert calls == {
        "secret_key": azure_admin_sdk.azure_management_oauth_secret.token_name,
        "module_name": "azure.mgmt.resource",
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "client_kwargs": {"base_url": "https://management.azure.com"},
        "token": "azure-token",
        "params": {"top": 5},
        "closed": True,
    }


def test_call_method_rejects_non_azure_mgmt_module() -> None:
    with pytest.raises(ValueError, match="azure.mgmt"):
        azure_admin_sdk._load_client_class("azure.identity", "DefaultAzureCredential")


def test_call_method_closes_client_when_method_raises(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class ResourceGroups:
        def get(self, **_params: Any) -> None:
            raise RuntimeError("azure failed")

    class ResourceManagementClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.resource_groups = ResourceGroups()

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(
        azure_admin_sdk.importlib,
        "import_module",
        lambda _module_name: SimpleNamespace(
            ResourceManagementClient=ResourceManagementClient
        ),
    )
    monkeypatch.setattr(azure_admin_sdk.secrets, "get", lambda _key: "azure-token")

    with pytest.raises(RuntimeError, match="azure failed"):
        azure_admin_sdk.call_method(
            module_name="azure.mgmt.resource",
            client_class="ResourceManagementClient",
            subscription_id="00000000-0000-0000-0000-000000000000",
            method_path="resource_groups",
            method_name="get",
        )

    assert calls == {"closed": True}


def test_call_method_rejects_item_paged_result(monkeypatch) -> None:
    class ResourceGroups:
        def list(self, **_params: Any) -> FakeItemPaged:
            return FakeItemPaged([FakeAzureModel({"name": "rg-test"})])

    class ResourceManagementClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.resource_groups = ResourceGroups()

        def close(self) -> None:
            pass

    monkeypatch.setattr(azure_admin_sdk, "ItemPaged", FakeItemPaged)
    monkeypatch.setattr(
        azure_admin_sdk.importlib,
        "import_module",
        lambda _module_name: SimpleNamespace(
            ResourceManagementClient=ResourceManagementClient
        ),
    )
    monkeypatch.setattr(azure_admin_sdk.secrets, "get", lambda _key: "azure-token")

    with pytest.raises(ValueError, match="call_paginated_method"):
        azure_admin_sdk.call_method(
            module_name="azure.mgmt.resource",
            client_class="ResourceManagementClient",
            subscription_id="00000000-0000-0000-0000-000000000000",
            method_path="resource_groups",
            method_name="list",
        )


def test_call_paginated_method_materializes_item_paged(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class ResourceGroups:
        def list(self, **params: Any) -> FakeItemPaged:
            calls["params"] = params
            return FakeItemPaged(
                [
                    FakeAzureModel({"name": "rg-one"}),
                    FakeAzureModel({"name": "rg-two"}),
                ]
            )

    class ResourceManagementClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.resource_groups = ResourceGroups()

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(azure_admin_sdk, "ItemPaged", FakeItemPaged)
    monkeypatch.setattr(
        azure_admin_sdk.importlib,
        "import_module",
        lambda _module_name: SimpleNamespace(
            ResourceManagementClient=ResourceManagementClient
        ),
    )
    monkeypatch.setattr(azure_admin_sdk.secrets, "get", lambda _key: "azure-token")

    result = azure_admin_sdk.call_paginated_method(
        module_name="azure.mgmt.resource",
        client_class="ResourceManagementClient",
        subscription_id="00000000-0000-0000-0000-000000000000",
        method_path="resource_groups",
        method_name="list",
        params={"top": 2},
    )

    assert result == [{"name": "rg-one"}, {"name": "rg-two"}]
    assert calls == {"params": {"top": 2}, "closed": True}


@pytest.mark.parametrize(
    ("method_path", "method_name", "message"),
    [
        ("resource_groups.", "list", "Method path segment"),
        ("resource_groups._raw", "list", "Method path segment"),
        ("resource_groups", "_delete", "Method name"),
        ("resource_groups", "", "Method name"),
    ],
)
def test_call_method_rejects_private_or_empty_method_names(
    method_path: str, method_name: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        azure_admin_sdk._resolve_method(
            SimpleNamespace(resource_groups=SimpleNamespace()),
            method_path,
            method_name,
        )
