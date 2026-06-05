from types import SimpleNamespace
from typing import Any

import pytest
from tracecat_registry.integrations import azure_admin_sdk


def test_call_method_uses_oauth_token_and_dispatches_client_method(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class ResourceGroups:
        def list(self, **params: Any) -> dict[str, Any]:
            calls["params"] = params
            return {"value": [{"name": "rg-test"}]}

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
    }


def test_call_method_rejects_non_azure_mgmt_module() -> None:
    with pytest.raises(ValueError, match="azure.mgmt"):
        azure_admin_sdk._load_client_class("azure.identity", "DefaultAzureCredential")
