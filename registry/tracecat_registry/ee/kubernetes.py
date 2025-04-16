from tracecat.ee.sandbox.kubernetes import (
    list_kubernetes_pods,
    list_kubernetes_containers,
    run_kubectl_command,
)

from tracecat_registry import registry, RegistrySecret, secrets

from typing import Annotated
from typing_extensions import Doc


kubernetes_secret = RegistrySecret(name="kubernetes", keys=["KUBECONFIG_BASE64"])
"""Kubernetes credentials.

- name: `kubernetes`
- keys:
    - `KUBECONFIG_BASE64`: Base64 encoded kubeconfig YAML file.
"""


@registry.register(
    default_title="List pods",
    description="List all Kubernetes pods in a given namespace.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#list-pod-v1",
    namespace="ee.kubernetes",
    secrets=[kubernetes_secret],
)
def list_pods(
    namespace: Annotated[str, Doc("Namespace to list pods from.")],
) -> list[str]:
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    return list_kubernetes_pods(namespace, kubeconfig_base64)


@registry.register(
    default_title="List containers",
    description="List all containers in a given pod.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#list-pod-v1",
    namespace="ee.kubernetes",
    secrets=[kubernetes_secret],
)
def list_containers(
    pod: Annotated[str, Doc("Pod to list containers from.")],
    namespace: Annotated[str, Doc("Namespace to list containers from.")],
) -> list[str]:
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    return list_kubernetes_containers(pod, namespace, kubeconfig_base64)


@registry.register(
    default_title="Run kubectl command",
    description="Run a kubectl command on a Kubernetes cluster.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands",
    namespace="ee.kubectl",
    secrets=[kubernetes_secret],
)
def run_command(
    command: Annotated[str | list[str], Doc("Command to run.")],
    namespace: Annotated[str, Doc("Namespace to run the command in.")],
) -> dict[str, str | int]:
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    return run_kubectl_command(command, namespace, kubeconfig_base64)
