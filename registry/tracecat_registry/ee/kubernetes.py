from tracecat.ee.sandbox.kubernetes import (
    list_kubernetes_pods,
    list_kubernetes_containers,
    exec_kubernetes_pod,
)

from tracecat_registry import registry

from typing import Annotated, Any
from typing_extensions import Doc


@registry.register(
    default_title="List pods",
    description="List all Kubernetes pods in a given namespace.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#list-pod-v1",
    namespace="ee.kubernetes",
)
def list_pods(
    namespace: Annotated[str, Doc("Namespace to list pods from.")],
) -> list[str]:
    return list_kubernetes_pods(namespace)


@registry.register(
    default_title="List containers",
    description="List all containers in a given pod.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#list-pod-v1",
    namespace="ee.kubernetes",
)
def list_containers(
    pod: Annotated[str, Doc("Pod to list containers from.")],
    namespace: Annotated[str, Doc("Namespace to list containers from.")],
) -> list[str]:
    return list_kubernetes_containers(pod, namespace)


@registry.register(
    default_title="Execute command in pod",
    description="Execute commands in a Kubernetes pod.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.25/#exec-options",
    namespace="ee.kubernetes",
)
def execute_command(
    pod: Annotated[str, Doc("Pod to execute command in.")],
    command: Annotated[str | list[str], Doc("Command to execute.")],
    container: Annotated[
        str | None,
        Doc(
            "Container to execute command in. If not provided, the first container will be used."
        ),
    ] = None,
    namespace: Annotated[str, Doc("Namespace to execute command in.")] = "default",
    timeout: Annotated[int, Doc("Timeout for the command to execute.")] = 60,
) -> dict[str, Any]:
    result = exec_kubernetes_pod(
        pod=pod,
        command=command,
        container=container,
        namespace=namespace,
        timeout=timeout,
    )
    return result.model_dump()
