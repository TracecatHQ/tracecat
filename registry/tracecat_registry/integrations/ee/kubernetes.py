from tracecat.ee.sandbox.kubernetes import (
    list_kubernetes_pods,
    list_kubernetes_containers,
    list_kubernetes_pvc,
    list_kubernetes_secrets,
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
    default_title="List pvc",
    description="List all persistent volume claims in a given namespace.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#list-persistentvolumeclaim-v1",
    namespace="ee.kubernetes",
    secrets=[kubernetes_secret],
)
def list_pvc(
    namespace: Annotated[str, Doc("Namespace to list persistent volume claims from.")],
) -> list[str]:
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    return list_kubernetes_pvc(namespace, kubeconfig_base64)


@registry.register(
    default_title="List secrets",
    description="List all secrets in a given namespace.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#list-secret-v1",
    namespace="ee.kubernetes",
    secrets=[kubernetes_secret],
)
def list_secrets(
    namespace: Annotated[str, Doc("Namespace to list secrets from.")],
) -> list[str]:
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    return list_kubernetes_secrets(namespace, kubeconfig_base64)


@registry.register(
    default_title="Run kubectl command",
    description="Run a kubectl command on a Kubernetes cluster.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands",
    namespace="ee.kubernetes",
    secrets=[kubernetes_secret],
)
def run_command(
    command: Annotated[str | list[str], Doc("Command to run.")],
    namespace: Annotated[str, Doc("Namespace to run the command in.")],
    dry_run: Annotated[
        bool, Doc("Whether to dry run the command client-side.")
    ] = False,
    stdin: Annotated[
        str | None, Doc("Optional string to pass to the command's standard input.")
    ] = None,
) -> dict[str, str | int]:
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    return run_kubectl_command(
        command, namespace, kubeconfig_base64, dry_run=dry_run, stdin=stdin
    )
