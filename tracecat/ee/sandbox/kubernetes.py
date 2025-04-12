"""Kubernetes pods operations.

Security hardening:
- Do not allow default namespace in kubeconfig contexts and functions
- Do not allow access to the current namespace
- Must be provided with kubeconfig as a secret (cannot be loaded from environment or default locations)

References
----------
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1PodList.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1PodSpec.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1Pod.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1Container.md
- https://martinheinz.dev/blog/73
"""

import base64
from typing import Any

from kubernetes import client, config
from kubernetes.client.models import V1Container, V1Pod, V1PodList, V1PodSpec
from kubernetes.stream import stream
from yaml import safe_load

from tracecat.logger import logger


def _get_k8s_client(kubeconfig_base64: str) -> client.CoreV1Api:
    """Get Kubernetes client with explicit configuration.

    Args:
        kubeconfig_base64: Base64 encoded kubeconfig YAML file.

    Returns:
        CoreV1Api: Kubernetes API client

    Raises:
        ValueError: If kubeconfig is invalid
    """

    # kubeconfig must be provided
    if not kubeconfig_base64:
        logger.warning("Empty kubeconfig provided", security_event="empty_kubeconfig")
        raise ValueError("kubeconfig cannot be empty")

    # Decode base64 kubeconfig YAML file
    kubeconfig_dict = safe_load(base64.b64decode(kubeconfig_base64))
    if not kubeconfig_dict:
        logger.warning(
            "Empty kubeconfig dictionary after decoding",
            security_event="invalid_kubeconfig",
        )
        raise ValueError("kubeconfig cannot be empty")

    contexts = kubeconfig_dict.get("contexts", [])
    if not contexts:
        logger.warning("Kubeconfig contains no contexts", security_event="no_contexts")
        raise ValueError("kubeconfig must contain at least one context")

    # Cannot contain default namespace
    for context in contexts:
        if context.get("namespace") == "default":
            logger.warning(
                "Kubeconfig contains default namespace",
                security_event="default_namespace_in_context",
                context_name=context.get("name"),
            )
            raise ValueError("kubeconfig cannot contain default namespace")

    # Load from explicit file, never from environment or default locations
    # NOTE: This is critical. We must not allow Kubernetes' default behavior of
    # using the kubeconfig from the environment.
    config.load_kube_config_from_dict(config_dict=kubeconfig_dict)

    logger.info(
        "Successfully validated and loaded kubeconfig",
        security_event="kubeconfig_loaded",
        contexts_count=len(contexts),
    )
    return client.CoreV1Api()


def _validate_access_allowed(namespace: str) -> None:
    """Validate if access to the namespace is allowed.

    Args:
        namespace: Namespace to check access for

    Raises:
        PermissionError: If access to the namespace is not allowed
    """

    logger.info(
        "Validating namespace access permissions",
        namespace=namespace,
        security_event="namespace_validation",
    )

    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            current_namespace = f.read().strip()
    except FileNotFoundError as e:
        logger.warning(
            "Kubernetes service account namespace file not found",
            security_event="missing_namespace_file",
        )
        raise FileNotFoundError(
            "Kubernetes service account namespace file not found"
        ) from e

    # Cannot be default namespace
    if current_namespace == "default":
        logger.warning(
            "Attempted operation on default namespace",
            security_event="default_namespace_operation",
        )
        raise PermissionError(
            "Tracecat does not allow Kubernetes operations on the default namespace"
        )

    # Check if current namespace is the same as the provided namespace
    if current_namespace == namespace:
        logger.warning(
            "Attempted operation on current namespace",
            current_namespace=current_namespace,
            security_event="current_namespace_operation",
        )
        raise PermissionError(
            f"Tracecat does not allow Kubernetes operations on the current namespace {current_namespace!r}"
        )

    logger.info(
        "Namespace access validated",
        namespace=namespace,
        current_namespace=current_namespace,
        security_event="namespace_access_granted",
    )


def list_kubernetes_pods(namespace: str, kubeconfig_base64: str) -> list[str]:
    """List all pods in the given namespace.

    Args:
        namespace : str
            The namespace to list pods from. Must not be the current namespace.
        kubeconfig_base64 : str
            Base64 encoded kubeconfig YAML file. Required for security isolation.

    Returns:
        list[str]: List of pod names in the namespace.

    Raises:
        PermissionError: If trying to access current namespace
        ValueError: If no pods found or invalid arguments
    """
    logger.info(
        "Listing kubernetes pods", namespace=namespace, security_event="list_pods"
    )
    _validate_access_allowed(namespace)
    client = _get_k8s_client(kubeconfig_base64)

    pods: V1PodList = client.list_namespaced_pod(namespace=namespace)
    if pods.items is None:
        logger.warning("No pods found in namespace", namespace=namespace)
        raise ValueError(f"No pods found in namespace {namespace}")
    items: list[V1Pod] = pods.items
    pod_names = [pod.metadata.name for pod in items]  # type: ignore

    logger.info(
        "Successfully listed pods",
        namespace=namespace,
        pod_count=len(pod_names),
        security_event="pods_listed",
    )
    return pod_names


def list_kubernetes_containers(
    pod: str, namespace: str, kubeconfig_base64: str
) -> list[str]:
    """List all containers in a given pod.

    Args:
        pod : str
            Name of the pod to list containers from.
        namespace : str
            Namespace where the pod is located. Must not be the current namespace.
        kubeconfig_base64 : str
            Base64 encoded kubeconfig YAML file. Required for security isolation.

    Returns:
        list[str]: List of container names in the pod.

    Raises:
        PermissionError: If trying to access current namespace
        ValueError: If invalid pod or no containers found
    """
    logger.info(
        "Listing kubernetes containers",
        pod=pod,
        namespace=namespace,
        security_event="list_containers",
    )
    _validate_access_allowed(namespace)
    client = _get_k8s_client(kubeconfig_base64)

    pod_info: V1Pod = client.read_namespaced_pod(
        name=pod,
        namespace=namespace,
    )  # type: ignore
    if pod_info.spec is None:
        logger.warning("Pod has no spec", pod=pod, namespace=namespace)
        raise ValueError(f"Pod {pod} in namespace {namespace} has no spec")

    spec: V1PodSpec = pod_info.spec
    if spec.containers is None:
        logger.warning("Pod has no containers", pod=pod, namespace=namespace)
        raise ValueError(f"Pod {pod} in namespace {namespace} has no containers")

    containers: list[V1Container] = spec.containers
    container_names = [
        str(container.name) for container in containers if container.name is not None
    ]

    logger.info(
        "Successfully listed containers",
        pod=pod,
        namespace=namespace,
        container_count=len(container_names),
        security_event="containers_listed",
    )
    return container_names


def exec_kubernetes_pod(
    pod: str,
    command: str | list[str],
    namespace: str,
    kubeconfig_base64: str,
    container: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute a command in a Kubernetes pod.

    Args:
        pod : str
            Name of the pod to execute command in.
        command : str | list[str]
            Command to execute in the pod.
        namespace : str
            Namespace where the pod is located. Must not be the current namespace.
        kubeconfig_base64 : str
            Base64 encoded kubeconfig YAML file. Required for security isolation.
        container : str | None, default=None
            Name of the container to execute command in. If None, uses the first container.
        timeout : int, default=60
            Timeout in seconds for the command execution.

    Returns:
        KubernetesResult: Object containing stdout and stderr from the command.

    Raises:
        PermissionError: If trying to access current namespace
        RuntimeError: If the command execution fails
        ValueError: If invalid arguments provided
    """
    cmd = command if isinstance(command, list) else [command]
    logger.info(
        "Executing command in kubernetes pod",
        pod=pod,
        namespace=namespace,
        command=cmd,
        container=container,
        security_event="pod_exec",
    )

    _validate_access_allowed(namespace)

    # Convert string command to list
    if isinstance(command, str):
        command = [command]

    client = _get_k8s_client(kubeconfig_base64)

    if container is None:
        containers = list_kubernetes_containers(pod, namespace, kubeconfig_base64)
        container = containers[0]
        logger.info(
            "Using first container",
            pod=pod,
            namespace=namespace,
            container=container,
            security_event="default_container",
        )

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
                security_event="pod_exec_stderr",
            )
            raise RuntimeError(f"Got stderr from Kubernetes pod exec: {stderr!r}")

        logger.info(
            "Successfully executed command",
            pod=pod,
            namespace=namespace,
            container=container,
            stdout_lines=len(stdout),
            security_event="pod_exec_success",
        )

        return {
            "pod": pod,
            "container": container,
            "namespace": namespace,
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
        }

    except Exception as e:
        logger.warning(
            "Unexpected error executing Kubernetes command",
            pod=pod,
            container=container,
            namespace=namespace,
            command=command,
            error=str(e),
            security_event="pod_exec_error",
        )
        raise e
