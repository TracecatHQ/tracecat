"""Kubernetes pods operations.

References
----------
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1PodList.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1PodSpec.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1Pod.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1Container.md
- https://martinheinz.dev/blog/73
"""

from kubernetes import config
from kubernetes.client import CoreV1Api
from kubernetes.client.models import V1Container, V1Pod, V1PodList, V1PodSpec
from kubernetes.stream import stream
from pydantic import BaseModel

from tracecat.logger import logger


class KubernetesResult(BaseModel):
    """Result from running a command in a Kubernetes pod.

    Parameters
    ----------
    pod: str
        Pod name that was used.
    container: str
        Container name that was used.
    namespace: str
        Namespace that the pod is in.
    command: list[str]
        Command that was executed.
    stdout: list[str]
        Standard output lines from the container.
    stderr: list[str]
        Standard error lines from the container.
    """

    pod: str
    container: str
    namespace: str
    command: list[str]
    stdout: list[str] | None = None
    stderr: list[str] | None = None


def list_kubernetes_pods(namespace: str = "default") -> list[str]:
    """List all pods in the given namespace.

    Args:
        namespace : str, default="default"
            The namespace to list pods from.

    Returns:
        list[str]: List of pod names in the namespace.
    """
    config.load_kube_config()
    client = CoreV1Api()
    pods: V1PodList = client.list_namespaced_pod(namespace=namespace)
    if pods.items is None:
        raise ValueError(f"No pods found in namespace {namespace}")
    items: list[V1Pod] = pods.items
    return [pod.metadata.name for pod in items]  # type: ignore


def list_kubernetes_containers(pod: str, namespace: str = "default") -> list[str]:
    """List all containers in a given pod.

    Args:
        pod : str
            Name of the pod to list containers from.
        namespace : str, default="default"
            Namespace where the pod is located.

    Returns:
        list[str]: List of container names in the pod.
    """
    config.load_kube_config()
    client = CoreV1Api()

    pod_info: V1Pod = client.read_namespaced_pod(
        name=pod,
        namespace=namespace,
    )  # type: ignore
    if pod_info.spec is None:
        raise ValueError(f"Pod {pod} in namespace {namespace} has no spec")

    spec: V1PodSpec = pod_info.spec
    if spec.containers is None:
        raise ValueError(f"Pod {pod} in namespace {namespace} has no containers")

    containers: list[V1Container] = spec.containers
    return [container.name for container in containers]  # type: ignore


def exec_kubernetes_pod(
    pod: str,
    command: list[str],
    container: str | None = None,
    namespace: str = "default",
    timeout: int = 60,
) -> KubernetesResult:
    """Execute a command in a Kubernetes pod.

    Args:
        pod : str
            Name of the pod to execute command in.
        command : list[str]
            Command to execute in the pod.
        container : str | None, default=None
            Name of the container to execute command in. If None, uses the first container.
        namespace : str, default="default"
            Namespace where the pod is located.
        timeout : int, default=60
            Timeout in seconds for the command execution.

    Returns:
        KubernetesResult: Object containing stdout and stderr from the command.

    Raises:
        RuntimeError: If the command execution fails.
    """
    config.load_kube_config()
    client = CoreV1Api()

    if container is None:
        containers = list_kubernetes_containers(pod, namespace)
        container = containers[0]

    try:
        resp = stream(
            client.connect_get_namespaced_pod_exec(
                name=pod,
                namespace=namespace,
                command=command,
                container=container,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
                _request_timeout=timeout,
            )
        )
        # Split output into lines
        stdout = resp.read_stdout().splitlines() if resp.peek_stdout() else []
        stderr = resp.read_stderr().splitlines() if resp.peek_stderr() else []

        if stderr:
            logger.warning(
                "Unexpected stderr output from Kubernetes pod exec",
                pod=pod,
                container=container,
                namespace=namespace,
                command=command,
                stderr=stderr,
            )
            raise RuntimeError(
                "Unexpected stderr output from Kubernetes pod exec: {stderr!r}"
            )

        return KubernetesResult(
            pod=pod,
            container=container,
            namespace=namespace,
            command=command,
            stdout=stdout,
            stderr=stderr,
        )

    except Exception as e:
        logger.warning(
            "Failed to execute Kubernetes command",
            pod=pod,
            container=container,
            namespace=namespace,
            command=command,
        )
        raise RuntimeError(
            f"Failed to execute command in pod {namespace}/{pod}/{container}: {str(e)}"
        ) from e
