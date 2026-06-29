from typing import Any

import pytest
import yaml
from kubernetes import config as kube_config
from tracecat_registry.integrations import kubernetes_sdk


def _kubeconfig(**user_overrides: Any) -> str:
    user = {"token": "kube-token", **user_overrides}
    return yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": "cluster",
                    "cluster": {
                        "server": "https://kubernetes.example.com",
                        "certificate-authority-data": "Y2E=",
                    },
                }
            ],
            "contexts": [
                {
                    "name": "ctx",
                    "context": {"cluster": "cluster", "user": "user"},
                },
                {
                    "name": "secret-context",
                    "context": {"cluster": "cluster", "user": "user"},
                },
            ],
            "current-context": "ctx",
            "users": [{"name": "user", "user": user}],
        }
    )


def test_call_api_uses_isolated_kubeconfig_and_passes_api_client(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class Configuration:
        pass

    class ApiClient:
        def __init__(self, *, configuration: Configuration) -> None:
            calls["api_client_configuration"] = configuration

        def sanitize_for_serialization(self, result: Any) -> Any:
            calls["sanitized"] = result
            return {"items": result["items"]}

    class Loader:
        def __init__(self, *, config_dict: dict[str, Any], active_context: str) -> None:
            calls["config_dict"] = config_dict
            calls["active_context"] = active_context

        def load_and_set(self, configuration: Configuration) -> None:
            calls["loader_configuration"] = configuration

    class CoreV1Api:
        def __init__(self, *, api_client: ApiClient) -> None:
            calls["api_client"] = api_client

        def list_namespaced_pod(self, **params: Any) -> dict[str, Any]:
            calls["params"] = params
            return {"items": [{"metadata": {"name": "pod-a"}}]}

    monkeypatch.setattr(kubernetes_sdk.client, "Configuration", Configuration)
    monkeypatch.setattr(kubernetes_sdk.client, "ApiClient", ApiClient)
    monkeypatch.setattr(kubernetes_sdk.client, "CoreV1Api", CoreV1Api)
    monkeypatch.setattr(kubernetes_sdk, "KubeConfigLoader", Loader)
    monkeypatch.setattr(kubernetes_sdk.secrets, "get", lambda key: _kubeconfig())
    monkeypatch.setattr(
        kubernetes_sdk.secrets,
        "get_or_default",
        lambda key: "secret-context" if key == "KUBECONFIG_CONTEXT" else None,
    )

    result = kubernetes_sdk.call_api(
        api_class="CoreV1Api",
        method_name="list_namespaced_pod",
        params={"namespace": "default"},
    )

    assert result == {"items": [{"metadata": {"name": "pod-a"}}]}
    assert calls["config_dict"]["users"][0]["user"]["token"] == "kube-token"
    assert calls["active_context"] == "secret-context"
    assert calls["api_client"].__class__ is ApiClient
    assert calls["api_client_configuration"] is calls["loader_configuration"]
    assert calls["params"] == {"namespace": "default"}


def test_build_api_client_uses_secret_context(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class Configuration:
        pass

    class ApiClient:
        def __init__(self, *, configuration: Configuration) -> None:
            calls["api_client_configuration"] = configuration

    class Loader:
        def __init__(
            self, *, config_dict: dict[str, Any], active_context: str | None
        ) -> None:
            calls["active_context"] = active_context

        def load_and_set(self, configuration: Configuration) -> None:
            calls["loader_configuration"] = configuration

    monkeypatch.setattr(kubernetes_sdk.client, "Configuration", Configuration)
    monkeypatch.setattr(kubernetes_sdk.client, "ApiClient", ApiClient)
    monkeypatch.setattr(kubernetes_sdk, "KubeConfigLoader", Loader)
    monkeypatch.setattr(kubernetes_sdk.secrets, "get", lambda key: _kubeconfig())
    monkeypatch.setattr(
        kubernetes_sdk.secrets,
        "get_or_default",
        lambda key: "secret-context" if key == "KUBECONFIG_CONTEXT" else None,
    )

    kubernetes_sdk._build_api_client()

    assert calls["active_context"] == "secret-context"
    assert calls["api_client_configuration"] is calls["loader_configuration"]


@pytest.mark.parametrize("blank_context", ["", "   "])
def test_build_api_client_normalizes_blank_secret_context(
    monkeypatch, blank_context: str
) -> None:
    """A blank KUBECONFIG_CONTEXT secret must fall back to `current-context`."""
    calls: dict[str, Any] = {}

    class Configuration:
        pass

    class ApiClient:
        def __init__(self, *, configuration: Configuration) -> None:
            pass

    class Loader:
        def __init__(
            self, *, config_dict: dict[str, Any], active_context: str | None
        ) -> None:
            calls["active_context"] = active_context

        def load_and_set(self, configuration: Configuration) -> None:
            pass

    monkeypatch.setattr(kubernetes_sdk.client, "Configuration", Configuration)
    monkeypatch.setattr(kubernetes_sdk.client, "ApiClient", ApiClient)
    monkeypatch.setattr(kubernetes_sdk, "KubeConfigLoader", Loader)
    monkeypatch.setattr(kubernetes_sdk.secrets, "get", lambda key: _kubeconfig())
    monkeypatch.setattr(
        kubernetes_sdk.secrets,
        "get_or_default",
        lambda key: blank_context if key == "KUBECONFIG_CONTEXT" else None,
    )

    kubernetes_sdk._build_api_client()

    assert calls["active_context"] is None


@pytest.mark.parametrize(
    ("cluster_overrides", "user_overrides", "message"),
    [
        (
            {"certificate-authority": "/var/run/secrets/kubernetes.io/ca.crt"},
            {},
            "certificate-authority",
        ),
        ({}, {"client-certificate": "/tmp/client.crt"}, "client-certificate"),
        ({}, {"client-key": "/tmp/client.key"}, "client-key"),
        ({}, {"tokenFile": "/var/run/secrets/kubernetes.io/token"}, "tokenFile"),
        ({}, {"exec": {"command": "kubectl"}}, "exec"),
        ({}, {"auth-provider": {"name": "gcp"}}, "auth-provider"),
    ],
)
def test_validate_rejects_file_backed_and_dynamic_credentials(
    cluster_overrides: dict[str, Any],
    user_overrides: dict[str, Any],
    message: str,
) -> None:
    config = yaml.safe_load(_kubeconfig(**user_overrides))
    config["clusters"][0]["cluster"].update(cluster_overrides)

    with pytest.raises(ValueError, match=message):
        kubernetes_sdk._validate_no_executor_credentials(config)


def test_validate_ignores_inactive_context_credentials() -> None:
    """Unused contexts with exec/file-backed credentials must not be rejected."""
    config = yaml.safe_load(_kubeconfig())
    config["clusters"].append(
        {
            "name": "other-cluster",
            "cluster": {
                "server": "https://other.example.com",
                "certificate-authority-data": "Y2E=",
            },
        }
    )
    config["users"].append(
        {"name": "other-user", "user": {"exec": {"command": "kubectl"}}}
    )
    config["contexts"].append(
        {
            "name": "other-ctx",
            "context": {"cluster": "other-cluster", "user": "other-user"},
        }
    )

    # current-context is the safe inline-token "ctx"; the unsafe context is inactive.
    kubernetes_sdk._validate_no_executor_credentials(config)

    # Selecting the unsafe context explicitly is still rejected.
    with pytest.raises(ValueError, match="exec"):
        kubernetes_sdk._validate_no_executor_credentials(
            config, active_context="other-ctx"
        )


def test_validate_rejects_unknown_active_context() -> None:
    config = yaml.safe_load(_kubeconfig())
    with pytest.raises(ValueError, match="not found"):
        kubernetes_sdk._validate_no_executor_credentials(
            config, active_context="missing"
        )


def test_never_calls_ambient_kubernetes_credential_loaders(monkeypatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("ambient Kubernetes credential loader was called")

    class Configuration:
        set_default = staticmethod(fail)

    class ApiClient:
        def __init__(self, *, configuration: Configuration) -> None:
            pass

    class Loader:
        def __init__(
            self, *, config_dict: dict[str, Any], active_context: str | None
        ) -> None:
            pass

        def load_and_set(self, configuration: Configuration) -> None:
            pass

    monkeypatch.setattr(kubernetes_sdk.client, "Configuration", Configuration)
    monkeypatch.setattr(kubernetes_sdk.client, "ApiClient", ApiClient)
    monkeypatch.setattr(kubernetes_sdk, "KubeConfigLoader", Loader)
    monkeypatch.setattr(kubernetes_sdk.secrets, "get", lambda key: _kubeconfig())
    monkeypatch.setattr(kubernetes_sdk.secrets, "get_or_default", lambda key: None)
    monkeypatch.setattr(kube_config, "load_kube_config", fail, raising=False)
    monkeypatch.setattr(kube_config, "new_client_from_config", fail, raising=False)
    monkeypatch.setattr(kube_config, "load_incluster_config", fail)

    kubernetes_sdk._build_api_client()
