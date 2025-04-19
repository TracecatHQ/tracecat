"""Kubernetes pods operations.

Security hardening:
- Do not allow default namespace in kubeconfig contexts and functions
- Do not allow access to the current namespace (if running in a pod)
- Must be provided with kubeconfig as a secret (cannot be loaded from environment or default locations)
- Assumes that the pod has service account token and namespace file mounted (i.e. automountServiceAccountToken: True in the pod spec)

References
----------
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1PodList.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1PodSpec.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1Pod.md
- https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V1Container.md
- https://martinheinz.dev/blog/73
"""

import base64
import os
import pathlib
import shlex
import subprocess
import tempfile
from typing import overload

from kubernetes import client, config
from kubernetes.client.models import V1Container, V1Pod, V1PodList, V1PodSpec
from yaml import safe_dump, safe_load

from tracecat.logger import logger


@overload
def _decode_kubeconfig(kubeconfig_base64: str) -> dict: ...


@overload
def _decode_kubeconfig(kubeconfig_base64: str, as_yaml: bool = True) -> str: ...


def _decode_kubeconfig(kubeconfig_base64: str, as_yaml: bool = False) -> dict | str:
    """Decode base64 kubeconfig YAML file.

    Args:
        kubeconfig_base64: Base64 encoded kubeconfig YAML file.
        as_yaml: If True, return the decoded kubeconfig YAML file as bytes.

    Returns:
        dict: Decoded kubeconfig YAML file.
        str: Decoded kubeconfig YAML file as string.

    Raises:
        ValueError: If kubeconfig is invalid
    """
    # Decode base64 kubeconfig YAML file
    kubeconfig_yaml = base64.b64decode(kubeconfig_base64 + "==")
    kubeconfig_dict = safe_load(kubeconfig_yaml)
    logger.info(
        "Loaded kubeconfig YAML into JSON with fields", fields=kubeconfig_dict.keys()
    )

    if not isinstance(kubeconfig_dict, dict):
        logger.warning("kubeconfig is not a dictionary")
        raise ValueError("kubeconfig must be a dictionary")

    if not kubeconfig_dict:
        logger.warning("Empty kubeconfig dictionary after decoding")
        raise ValueError("kubeconfig cannot be empty")

    contexts = kubeconfig_dict.get("contexts", [])
    if not contexts:
        logger.warning("Kubeconfig contains no contexts")
        raise ValueError("kubeconfig must contain at least one context")

    # Cannot contain default namespace
    for context in contexts:
        if context.get("namespace") == "default":
            logger.warning(
                "Kubeconfig contains default namespace",
                context_name=context.get("name"),
            )
            raise ValueError("kubeconfig cannot contain default namespace")

    if as_yaml:
        kubeconfig_yaml_str = safe_dump(kubeconfig_dict)
        return kubeconfig_yaml_str
    return kubeconfig_dict


def _get_kubernetes_client(kubeconfig_base64: str) -> client.CoreV1Api:
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
        logger.warning("Empty kubeconfig provided")
        raise ValueError("kubeconfig cannot be empty")

    # Load from explicit file, never from environment or default locations
    # NOTE: This is critical. We must not allow Kubernetes' default behavior of
    # using the kubeconfig from the environment.
    kubeconfig_dict = _decode_kubeconfig(kubeconfig_base64)
    config.load_kube_config_from_dict(config_dict=kubeconfig_dict)

    logger.info("Successfully validated and loaded kubeconfig")
    return client.CoreV1Api()


def _validate_access_allowed(namespace: str) -> None:
    """Validate if access to the namespace is allowed.

    Args:
        namespace: Namespace to check access for

    Raises:
        PermissionError: If access to the namespace is not allowed
    """

    logger.info("Validating namespace access permissions", namespace=namespace)
    current_namespace = None

    # Cannot be default namespace
    if namespace == "default":
        logger.warning("Attempted operation on default namespace")
        raise PermissionError(
            "Tracecat does not allow Kubernetes operations on the default namespace"
        )

    # Check if current namespace is the same as the provided namespace
    if "KUBERNETES_SERVICE_HOST" in os.environ:
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
                current_namespace = f.read().strip()
        except FileNotFoundError as e:
            logger.warning("Kubernetes service account namespace file not found")
            raise FileNotFoundError(
                "Kubernetes service account namespace file not found"
            ) from e

        # Check if current namespace is the same as the provided namespace
        if current_namespace == namespace:
            logger.warning(
                "Attempted operation on current namespace",
                current_namespace=current_namespace,
            )
            raise PermissionError(
                f"Tracecat does not allow Kubernetes operations on the current namespace {current_namespace!r}"
            )

    logger.info(
        "Namespace access validated",
        namespace=namespace,
        current_namespace=current_namespace,
    )


### Pods


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
    logger.info("Listing kubernetes pods", namespace=namespace)
    _validate_access_allowed(namespace)
    client = _get_kubernetes_client(kubeconfig_base64)

    pods: V1PodList = client.list_namespaced_pod(namespace=namespace)
    if pods.items is None:
        logger.warning("No pods found in namespace", namespace=namespace)
        raise ValueError(f"No pods found in namespace {namespace}")
    items: list[V1Pod] = pods.items
    pod_names = [pod.metadata.name for pod in items]  # type: ignore

    logger.info(
        "Successfully listed pods", namespace=namespace, pod_count=len(pod_names)
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
    logger.info("Listing kubernetes containers", pod=pod, namespace=namespace)
    _validate_access_allowed(namespace)
    client = _get_kubernetes_client(kubeconfig_base64)

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

    logger.info("Successfully listed containers", pod=pod, namespace=namespace)
    return container_names


def run_kubectl_command(
    command: str | list[str],
    namespace: str,
    kubeconfig_base64: str,
    dry_run: bool = False,
    stdin: str | None = None,
) -> dict[str, str | int]:
    """Run a kubectl command."""
    _validate_access_allowed(namespace)

    # Convert string command to list
    if isinstance(command, str):
        command = shlex.split(command)

    kubeconfig_yaml = _decode_kubeconfig(kubeconfig_base64, as_yaml=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        kubeconfig_path = pathlib.Path(temp_dir) / "kubeconfig.yaml"
        with open(kubeconfig_path, "w") as f:
            f.write(kubeconfig_yaml)

        args = ["kubectl", "--kubeconfig", kubeconfig_path]
        if dry_run:
            args.append("--dry-run=client")
        args.extend(command)

        output = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            input=stdin,
        )
        return {
            "stdout": output.stdout,
            "stderr": output.stderr,
            "returncode": output.returncode,
        }
