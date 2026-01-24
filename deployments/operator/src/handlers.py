"""Kopf handlers for TracecatWorkerPool CRD."""

from __future__ import annotations

import logging
from typing import TypedDict, cast

import kopf
from kubernetes import client
from kubernetes.client.rest import ApiException

from .resources import (
    build_deployment,
    build_scaled_object,
    build_trigger_authentication,
)

logger = logging.getLogger(__name__)


class WorkerStatus(TypedDict, total=False):
    """Status for a single worker type."""

    deployment: str
    scaledObject: str
    readyReplicas: int


class ReconcileResult(TypedDict):
    """Result returned from reconciliation."""

    workers: dict[str, WorkerStatus]


def apply_deployment(
    api: client.AppsV1Api,
    namespace: str,
    deployment: dict[str, object],
) -> None:
    """Create or update a deployment."""
    metadata = cast(dict[str, object], deployment["metadata"])
    name = cast(str, metadata["name"])
    try:
        api.patch_namespaced_deployment(
            name=name,
            namespace=namespace,
            body=deployment,
        )
        logger.info(f"Patched deployment {name}")
    except ApiException as e:
        if e.status == 404:
            api.create_namespaced_deployment(
                namespace=namespace,
                body=deployment,
            )
            logger.info(f"Created deployment {name}")
        else:
            raise


def delete_deployment(
    api: client.AppsV1Api,
    namespace: str,
    name: str,
) -> None:
    """Delete a deployment if it exists."""
    try:
        api.delete_namespaced_deployment(
            name=name,
            namespace=namespace,
        )
        logger.info(f"Deleted deployment {name}")
    except ApiException as e:
        if e.status != 404:
            raise


def apply_custom_object(
    api: client.CustomObjectsApi,
    group: str,
    version: str,
    plural: str,
    namespace: str,
    body: dict[str, object],
) -> None:
    """Create or update a custom object."""
    metadata = cast(dict[str, object], body["metadata"])
    name = cast(str, metadata["name"])
    try:
        api.patch_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            name=name,
            body=body,
        )
        logger.info(f"Patched {plural}/{name}")
    except ApiException as e:
        if e.status == 404:
            api.create_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                body=body,
            )
            logger.info(f"Created {plural}/{name}")
        else:
            raise


def delete_custom_object(
    api: client.CustomObjectsApi,
    group: str,
    version: str,
    plural: str,
    namespace: str,
    name: str,
) -> None:
    """Delete a custom object if it exists."""
    try:
        api.delete_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            name=name,
        )
        logger.info(f"Deleted {plural}/{name}")
    except ApiException as e:
        if e.status != 404:
            raise


@kopf.on.create("compute.tracecat.io", "v1alpha1", "tracecatworkerpools")  # pyright: ignore[reportArgumentType]
@kopf.on.update("compute.tracecat.io", "v1alpha1", "tracecatworkerpools")  # pyright: ignore[reportArgumentType]
async def reconcile(
    spec: kopf.Spec,
    name: str,
    namespace: str,
    body: kopf.Body,
    **_: object,
) -> ReconcileResult:
    """Reconcile Deployments and ScaledObjects for a worker pool."""
    apps_api = client.AppsV1Api()
    custom_api = client.CustomObjectsApi()

    workers_status: dict[str, WorkerStatus] = {}
    workers_config = spec.get("workers", {})
    autoscaling_config = spec.get("autoscaling", {})

    # Build and apply TriggerAuthentication if needed for KEDA
    if autoscaling_config.get("enabled"):
        trigger_auth = build_trigger_authentication(name, dict(spec))
        if trigger_auth:
            kopf.adopt(trigger_auth, owner=body)
            apply_custom_object(
                custom_api,
                group="keda.sh",
                version="v1alpha1",
                plural="triggerauthentications",
                namespace=namespace,
                body=trigger_auth,
            )

    # Process each worker type
    for worker_type in ["dsl", "executor", "agent"]:
        worker_spec = workers_config.get(worker_type, {})

        # Skip if not configured or disabled
        if not worker_spec:
            logger.info(f"Worker type {worker_type} not configured, skipping")
            continue

        if not worker_spec.get("enabled", True):
            logger.info(f"Worker type {worker_type} disabled, ensuring cleanup")
            deployment_name = f"{name}-{worker_type}-worker"
            delete_deployment(apps_api, namespace, deployment_name)

            if autoscaling_config.get("enabled"):
                scaled_object_name = f"{name}-{worker_type}-scaler"
                delete_custom_object(
                    custom_api,
                    group="keda.sh",
                    version="v1alpha1",
                    plural="scaledobjects",
                    namespace=namespace,
                    name=scaled_object_name,
                )
            continue

        # Build and apply Deployment
        deployment = build_deployment(name, worker_type, dict(worker_spec), dict(spec))
        if deployment:
            # Set owner reference for garbage collection
            kopf.adopt(deployment, owner=body)
            apply_deployment(apps_api, namespace, deployment)

            deployment_name = deployment["metadata"]["name"]
            workers_status[worker_type] = {"deployment": deployment_name}

            # Build and apply ScaledObject if autoscaling is enabled
            if autoscaling_config.get("enabled"):
                result = build_scaled_object(
                    name, worker_type, dict(worker_spec), dict(autoscaling_config), dict(spec)
                )
                if result is not None:
                    scaled_object: dict[str, object] = result[0]
                    if scaled_object:
                        kopf.adopt(scaled_object, owner=body)
                        apply_custom_object(
                            custom_api,
                            group="keda.sh",
                            version="v1alpha1",
                            plural="scaledobjects",
                            namespace=namespace,
                            body=scaled_object,
                        )
                        so_metadata = cast(dict[str, object], scaled_object["metadata"])
                        workers_status[worker_type]["scaledObject"] = cast(str, so_metadata["name"])

    logger.info(f"Reconciled worker pool {name}: {workers_status}")

    # Return status update
    return {"workers": workers_status}


@kopf.on.delete("compute.tracecat.io", "v1alpha1", "tracecatworkerpools")  # pyright: ignore[reportArgumentType]
async def cleanup(
    name: str,
    **_: object,
) -> None:
    """Clean up resources when a worker pool is deleted.

    Note: With owner references set via kopf.adopt(), Kubernetes will
    automatically garbage collect child resources. This handler is
    mainly for logging and any additional cleanup.
    """
    logger.info(f"Worker pool {name} deleted, child resources will be garbage collected")


@kopf.on.field("compute.tracecat.io", "v1alpha1", "tracecatworkerpools", field="spec.workers")  # pyright: ignore[reportArgumentType]
async def workers_changed(
    old: object,
    new: object,
    name: str,
    **_: object,
) -> None:
    """Handle changes to worker specifications."""
    if old == new:
        return

    logger.info(f"Worker spec changed for {name}, triggering reconciliation")
    # The main reconcile handler will be triggered by the update


@kopf.on.startup()  # pyright: ignore[reportArgumentType]
async def startup(**_: object) -> None:
    """Initialize the operator on startup."""
    logger.info("Tracecat Worker Pool Operator starting up")


@kopf.on.cleanup()  # pyright: ignore[reportArgumentType]
async def shutdown(**_: object) -> None:
    """Clean up on operator shutdown."""
    logger.info("Tracecat Worker Pool Operator shutting down")


@kopf.timer("compute.tracecat.io", "v1alpha1", "tracecatworkerpools", interval=60.0)  # pyright: ignore[reportArgumentType]
async def status_update(
    spec: kopf.Spec,
    name: str,
    namespace: str,
    status: kopf.Status,
    **_: object,
) -> ReconcileResult | None:
    """Periodically update status with deployment ready replicas."""
    apps_api = client.AppsV1Api()
    workers_config = spec.get("workers", {})
    workers_status = status.get("workers", {})

    updated = False
    new_status: dict[str, WorkerStatus] = {}

    for worker_type in ["dsl", "executor", "agent"]:
        worker_spec = workers_config.get(worker_type, {})
        if not worker_spec or not worker_spec.get("enabled", True):
            continue

        deployment_name = f"{name}-{worker_type}-worker"
        try:
            deployment = apps_api.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
            )
            ready_replicas = 0
            dep_status = getattr(deployment, "status", None)
            if dep_status is not None:
                ready_replicas = getattr(dep_status, "ready_replicas", 0) or 0

            current_status = workers_status.get(worker_type, {})
            if current_status.get("readyReplicas") != ready_replicas:
                updated = True

            new_status[worker_type] = {
                "deployment": deployment_name,
                "readyReplicas": ready_replicas,
            }

            # Preserve scaledObject reference if it exists
            if current_status.get("scaledObject"):
                new_status[worker_type]["scaledObject"] = current_status["scaledObject"]

        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to get deployment {deployment_name}: {e}")

    if updated:
        return {"workers": new_status}

    return None
