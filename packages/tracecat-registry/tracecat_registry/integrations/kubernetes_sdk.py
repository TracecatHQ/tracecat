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


def _find_named(entries: Any, name: str | None) -> dict[str, Any] | None:
    if not name or not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _validate_no_executor_credentials(
    config: dict[str, Any], active_context: str | None = None
) -> None:
    """Reject file-backed and dynamic credentials in the *selected* context only.

    The Kubernetes loader only reads the user and cluster referenced by the
    active context, so a full kubeconfig that also carries unrelated inactive
    contexts (with `exec`, `auth-provider`, or file-backed credentials) must
    still be accepted as long as the selected context is safe.
    """
    context_name = active_context or config.get("current-context")
    if not context_name:
        raise ValueError(
            "KUBECONFIG must define `current-context` or specify a context."
        )
    context_entry = _find_named(config.get("contexts"), context_name)
    if context_entry is None:
        raise ValueError(f"KUBECONFIG context `{context_name}` not found.")
    context = context_entry.get("context", {})
    if not isinstance(context, dict):
        raise ValueError(f"KUBECONFIG context `{context_name}` is malformed.")

    cluster_entry = _find_named(config.get("clusters"), context.get("cluster"))
    cluster = cluster_entry.get("cluster", {}) if cluster_entry else {}
    if isinstance(cluster, dict):
        for field in _CLUSTER_FILE_FIELDS:
            if field in cluster:
                raise ValueError(
                    f"KUBECONFIG field `{field}` is not allowed; use inline `{field}-data` instead."
                )

    user_entry = _find_named(config.get("users"), context.get("user"))
    user = user_entry.get("user", {}) if user_entry else {}
    if isinstance(user, dict):
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


def _build_api_client() -> client.ApiClient:
    config_dict = _load_kubeconfig()
    # Normalize a blank/whitespace secret to None so the loader falls back to
    # the kubeconfig `current-context` instead of seeking a context named "".
    active_context = (
        secrets.get_or_default("KUBECONFIG_CONTEXT") or ""
    ).strip() or None
    _validate_no_executor_credentials(config_dict, active_context)

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
) -> Any:
    params = params or {}
    api_client = _build_api_client()
    api = _load_api_class(api_class)(api_client=api_client)
    result = getattr(api, method_name)(**params)
    sanitized = api_client.sanitize_for_serialization(result)
    return cast(Any, to_jsonable_python(sanitized))
