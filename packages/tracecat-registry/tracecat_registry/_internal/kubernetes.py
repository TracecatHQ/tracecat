import os

import base64

from kubernetes import config
from yaml import safe_dump, safe_load
from tracecat.logger import logger


def decode_kubeconfig(kubeconfig_base64: str) -> str:
    """Decode base64 kubeconfig YAML string.

    Args:
        kubeconfig_base64: Base64 encoded kubeconfig YAML file.

    Returns:
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

    # Return the decoded kubeconfig YAML file as a dictionary

    # Validate the kubeconfig
    config.load_kube_config_from_dict(config_dict=kubeconfig_dict)
    logger.info("Successfully validated and loaded kubeconfig")

    return safe_dump(kubeconfig_dict)


def validate_namespace(namespace: str) -> None:
    """Validate if access to the namespace is allowed.

    This helper is intentionally located in the private ``_internal`` package so
    it is not treated as a user-defined function (UDF) by the registry loader.
    """

    logger.info("Validating namespace access permissions", namespace=namespace)

    # Disallow the default namespace outright
    if namespace == "default":
        logger.warning("Attempted operation on default namespace")
        raise PermissionError(
            "Tracecat does not allow Kubernetes operations on the default namespace"
        )

    current_namespace = None

    # When running inside a cluster look up the service account namespace
    if os.getenv("KUBERNETES_SERVICE_HOST") is not None:
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
                current_namespace = f.read().strip()
        except FileNotFoundError as e:
            logger.warning("Kubernetes service account namespace file not found")
            raise FileNotFoundError(
                "Kubernetes service account namespace file not found"
            ) from e

        if current_namespace == namespace:
            logger.warning(
                "Attempted operation on current namespace",
                current_namespace=current_namespace,
            )
            raise PermissionError(
                f"Tracecat does not allow Kubernetes operations on the current namespace {current_namespace!r}"
            )
    else:
        logger.info(
            "`KUBERNETES_SERVICE_HOST` env var not found; assuming access from outside the cluster."
        )

    logger.info(
        "Namespace access validated",
        namespace=namespace,
        current_namespace=current_namespace,
    )
