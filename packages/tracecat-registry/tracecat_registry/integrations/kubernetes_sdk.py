"""Generic interface for Kubernetes Python client with isolated credentials."""

from typing import Annotated, Any, cast

import yaml
from kubernetes import client
from kubernetes.config.kube_config import KubeConfigLoader
from pydantic import Field
from pydantic_core import to_jsonable_python

from tracecat_registry import RegistrySecret, registry, secrets

kubernetes_secret = RegistrySecret(
    name="kubernetes",
    keys=["KUBECONFIG"],
    optional_keys=["KUBECONFIG_CONTEXT"],
)
"""Kubernetes kubeconfig.

- name: `kubernetes`
- keys:
    - `KUBECONFIG` (YAML or JSON string)
- optional_keys:
    - `KUBECONFIG_CONTEXT`
"""

_CLUSTER_FILE_FIELDS = ("certificate-authority",)
_USER_FILE_FIELDS = ("client-certificate", "client-key", "tokenFile")
_DYNAMIC_USER_FIELDS = ("exec", "auth-provider")


def _load_kubeconfig() -> dict[str, Any]:
    raw = secrets.get("KUBECONFIG")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("KUBECONFIG must be a YAML or JSON object.")
    return cast(dict[str, Any], data)


def _validate_no_executor_credentials(config: dict[str, Any]) -> None:
    for cluster_entry in config.get("clusters", []):
        if not isinstance(cluster_entry, dict):
            continue
        cluster = cluster_entry.get("cluster", {})
        if not isinstance(cluster, dict):
            continue
        for field in _CLUSTER_FILE_FIELDS:
            if field in cluster:
                raise ValueError(
                    f"KUBECONFIG field `{field}` is not allowed; use inline `{field}-data` instead."
                )

    for user_entry in config.get("users", []):
        if not isinstance(user_entry, dict):
            continue
        user = user_entry.get("user", {})
        if not isinstance(user, dict):
            continue
        for field in _USER_FILE_FIELDS:
            if field in user:
                replacement = field.removesuffix("File")
                raise ValueError(
                    f"KUBECONFIG field `{field}` is not allowed; use inline `{replacement}-data` or token material instead."
                )
        for field in _DYNAMIC_USER_FIELDS:
            if field in user:
                raise ValueError(
                    f"KUBECONFIG dynamic credential field `{field}` is not allowed."
                )


def _build_api_client(context: str | None = None) -> client.ApiClient:
    config_dict = _load_kubeconfig()
    _validate_no_executor_credentials(config_dict)
    active_context = context or secrets.get_or_default("KUBECONFIG_CONTEXT")

    configuration = client.Configuration()
    loader = KubeConfigLoader(
        config_dict=config_dict,
        active_context=active_context,
    )
    loader.load_and_set(configuration)
    return client.ApiClient(configuration=configuration)


def _load_api_class(api_class: str) -> type[Any]:
    cls = getattr(client, api_class)
    if not isinstance(cls, type):
        raise TypeError(f"kubernetes.client.{api_class} is not a class.")
    return cls


@registry.register(
    default_title="Call API",
    description="Instantiate an isolated Kubernetes SDK client and call an API method.",
    display_group="Kubernetes SDK",
    doc_url="https://github.com/kubernetes-client/python",
    namespace="tools.kubernetes_sdk",
    secrets=[kubernetes_secret],
)
def call_api(
    api_class: Annotated[
        str,
        Field(..., description="Kubernetes API class, e.g. `CoreV1Api`."),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Kubernetes API method, e.g. `list_namespaced_pod`."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Kubernetes API method."),
    ] = None,
    context: Annotated[
        str | None,
        Field(
            ...,
            description="Optional kubeconfig context. Overrides KUBECONFIG_CONTEXT.",
        ),
    ] = None,
) -> Any:
    params = params or {}
    api_client = _build_api_client(context=context)
    api = _load_api_class(api_class)(api_client=api_client)
    result = getattr(api, method_name)(**params)
    sanitized = api_client.sanitize_for_serialization(result)
    return cast(Any, to_jsonable_python(sanitized))
