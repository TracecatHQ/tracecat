"""Resource builders for TracecatWorkerPool operator."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

WorkerType = Literal["dsl", "executor", "agent"]


class EnvVar(TypedDict, total=False):
    """Kubernetes environment variable."""

    name: str
    value: str
    valueFrom: dict[str, Any]


# Worker type to command mapping
WORKER_COMMANDS: dict[str, list[str]] = {
    "dsl": ["python", "-m", "tracecat.dsl.worker"],
    "executor": ["python", "-m", "tracecat.executor.worker"],
    "agent": ["python", "-m", "tracecat.agent.worker"],
}

# Default resources for each worker type
DEFAULT_RESOURCES: dict[str, dict[str, dict[str, str]]] = {
    "dsl": {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "1000m", "memory": "1Gi"},
    },
    "executor": {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "1000m", "memory": "1Gi"},
    },
    "agent": {
        "requests": {"cpu": "1000m", "memory": "2Gi"},
        "limits": {"cpu": "2000m", "memory": "4Gi"},
    },
}


def build_labels(
    pool_name: str, worker_type: str | WorkerType, extra: dict[str, str] | None = None
) -> dict[str, str]:
    """Build standard labels for a worker deployment."""
    labels = {
        "app.kubernetes.io/name": "tracecat-worker",
        "app.kubernetes.io/instance": pool_name,
        "app.kubernetes.io/component": f"{worker_type}-worker",
        "app.kubernetes.io/managed-by": "tracecat-operator",
        "tracecat.io/worker-pool": pool_name,
        "tracecat.io/worker-type": worker_type,
    }
    if extra:
        labels.update(extra)
    return labels


def build_common_env(spec: dict[str, Any], worker_spec: dict[str, Any]) -> list[EnvVar]:
    """Build common environment variables for a worker."""
    env: list[EnvVar] = []

    # Log level
    env.append({"name": "LOG_LEVEL", "value": "INFO"})
    env.append({"name": "TRACECAT__APP_ENV", "value": "production"})

    # Temporal configuration
    temporal = spec.get("temporal", {})
    if temporal.get("clusterUrl"):
        env.append({"name": "TEMPORAL__CLUSTER_URL", "value": temporal["clusterUrl"]})
    if temporal.get("namespace"):
        env.append({"name": "TEMPORAL__CLUSTER_NAMESPACE", "value": temporal["namespace"]})

    # Temporal API key from secret
    auth = temporal.get("auth", {})
    api_key_ref = auth.get("apiKeySecretRef")
    if api_key_ref:
        env.append(
            {
                "name": "TEMPORAL__API_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": api_key_ref["name"],
                        "key": api_key_ref.get("key", "apiKey"),
                    }
                },
            }
        )

    # Queue configuration - use worker-specific queue
    env.append({"name": "TEMPORAL__CLUSTER_QUEUE", "value": worker_spec["queue"]})

    # Database configuration
    database = spec.get("database", {})
    creds_ref = database.get("credentialsSecretRef", {})
    if creds_ref.get("name"):
        env.append(
            {
                "name": "TRACECAT__POSTGRES_USER",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": creds_ref["name"],
                        "key": creds_ref.get("usernameKey", "username"),
                    }
                },
            }
        )
        env.append(
            {
                "name": "TRACECAT__POSTGRES_PASSWORD",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": creds_ref["name"],
                        "key": creds_ref.get("passwordKey", "password"),
                    }
                },
            }
        )

        host = database.get("host", "postgres-rw")
        port = database.get("port", 5432)
        db_name = database.get("database", "app")
        env.append(
            {
                "name": "TRACECAT__DB_URI",
                "value": f"postgresql+psycopg://$(TRACECAT__POSTGRES_USER):$(TRACECAT__POSTGRES_PASSWORD)@{host}:{port}/{db_name}",
            }
        )

    # Redis configuration
    redis = spec.get("redis", {})
    if redis.get("url"):
        env.append({"name": "REDIS_URL", "value": redis["url"]})
    elif redis.get("urlSecretRef"):
        url_ref = redis["urlSecretRef"]
        env.append(
            {
                "name": "REDIS_URL",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": url_ref["name"],
                        "key": url_ref.get("key", "url"),
                    }
                },
            }
        )

    # Core secrets
    secrets = spec.get("secrets", {})
    core_ref = secrets.get("coreSecretRef", {})
    if core_ref.get("name"):
        secret_name = core_ref["name"]
        env.extend(
            [
                {
                    "name": "TRACECAT__DB_ENCRYPTION_KEY",
                    "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "dbEncryptionKey"}},
                },
                {
                    "name": "TRACECAT__SERVICE_KEY",
                    "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "serviceKey"}},
                },
                {
                    "name": "TRACECAT__SIGNING_SECRET",
                    "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "signingSecret"}},
                },
            ]
        )

    # Additional common env vars
    common_env = spec.get("env", {}).get("common", [])
    env.extend(common_env)

    return env


def build_dsl_env(spec: dict[str, Any], worker_spec: dict[str, Any]) -> list[EnvVar]:
    """Build DSL worker specific environment variables."""
    env = build_common_env(spec, worker_spec)

    # Context compression
    compression = worker_spec.get("contextCompression", {})
    env.append(
        {
            "name": "TRACECAT__CONTEXT_COMPRESSION_ENABLED",
            "value": str(compression.get("enabled", False)).lower(),
        }
    )
    env.append(
        {
            "name": "TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB",
            "value": str(compression.get("thresholdKb", 16)),
        }
    )

    # Threadpool max workers
    if worker_spec.get("threadpoolMaxWorkers"):
        env.append(
            {
                "name": "TRACECAT__THREADPOOL_MAX_WORKERS",
                "value": str(worker_spec["threadpoolMaxWorkers"]),
            }
        )

    return env


def build_executor_env(spec: dict[str, Any], worker_spec: dict[str, Any]) -> list[EnvVar]:
    """Build executor worker specific environment variables."""
    env = build_common_env(spec, worker_spec)

    # Executor specific settings
    env.append(
        {
            "name": "TRACECAT__EXECUTOR_BACKEND",
            "value": worker_spec.get("backend", "auto"),
        }
    )
    env.append(
        {
            "name": "TRACECAT__EXECUTOR_QUEUE",
            "value": worker_spec["queue"],
        }
    )

    if worker_spec.get("workerPoolSize"):
        env.append(
            {
                "name": "TRACECAT__EXECUTOR_WORKER_POOL_SIZE",
                "value": str(worker_spec["workerPoolSize"]),
            }
        )

    # Sandbox settings
    sandbox = worker_spec.get("sandbox", {})
    env.append(
        {
            "name": "TRACECAT__DISABLE_NSJAIL",
            "value": str(sandbox.get("disableNsjail", False)).lower(),
        }
    )
    env.append({"name": "TRACECAT__SANDBOX_NSJAIL_PATH", "value": "/usr/local/bin/nsjail"})
    env.append(
        {"name": "TRACECAT__SANDBOX_ROOTFS_PATH", "value": "/var/lib/tracecat/sandbox-rootfs"}
    )
    env.append({"name": "TRACECAT__SANDBOX_CACHE_DIR", "value": "/var/lib/tracecat/sandbox-cache"})

    # Context compression
    compression = worker_spec.get("contextCompression", {})
    env.append(
        {
            "name": "TRACECAT__CONTEXT_COMPRESSION_ENABLED",
            "value": str(compression.get("enabled", False)).lower(),
        }
    )
    env.append(
        {
            "name": "TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB",
            "value": str(compression.get("thresholdKb", 16)),
        }
    )

    # Secret masking
    env.append({"name": "TRACECAT__UNSAFE_DISABLE_SM_MASKING", "value": "false"})

    return env


def build_agent_env(spec: dict[str, Any], worker_spec: dict[str, Any]) -> list[EnvVar]:
    """Build agent worker specific environment variables."""
    env = build_common_env(spec, worker_spec)

    # Agent specific settings
    if worker_spec.get("maxConcurrentActivities"):
        env.append(
            {
                "name": "TRACECAT__AGENT_MAX_CONCURRENT_ACTIVITIES",
                "value": str(worker_spec["maxConcurrentActivities"]),
            }
        )

    return env


def build_deployment(
    pool_name: str,
    worker_type: str,
    worker_spec: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Build a Deployment resource for a worker type."""
    if not worker_spec.get("enabled", True):
        return {}

    deployment_name = f"{pool_name}-{worker_type}-worker"
    labels = build_labels(pool_name, worker_type)
    selector_labels = {
        "app.kubernetes.io/instance": pool_name,
        "app.kubernetes.io/component": f"{worker_type}-worker",
    }

    # Get image config
    image_config = spec.get("image", {})
    repository = image_config.get("repository", "ghcr.io/tracecathq/tracecat")
    tag = image_config.get("tag", "latest")
    pull_policy = image_config.get("pullPolicy", "IfNotPresent")

    # Get resources
    resources = worker_spec.get("resources", DEFAULT_RESOURCES.get(worker_type, {}))

    # Build environment variables based on worker type
    if worker_type == "dsl":
        env = build_dsl_env(spec, worker_spec)
    elif worker_type == "executor":
        env = build_executor_env(spec, worker_spec)
    elif worker_type == "agent":
        env = build_agent_env(spec, worker_spec)
    else:
        env = build_common_env(spec, worker_spec)

    # Base container spec
    container: dict[str, Any] = {
        "name": worker_type,
        "image": f"{repository}:{tag}",
        "imagePullPolicy": pull_policy,
        "command": WORKER_COMMANDS.get(
            worker_type, ["python", "-m", f"tracecat.{worker_type}.worker"]
        ),
        "env": env,
        "resources": resources,
    }

    # Add executor-specific volumes and security context
    volumes = []
    volume_mounts = []

    if worker_type == "executor":
        sandbox = worker_spec.get("sandbox", {})
        disable_nsjail = sandbox.get("disableNsjail", False)

        volumes = [
            {"name": "sandbox-cache", "emptyDir": {}},
            {"name": "tmp", "emptyDir": {}},
        ]
        volume_mounts = [
            {"name": "sandbox-cache", "mountPath": "/var/lib/tracecat/sandbox-cache"},
            {"name": "tmp", "mountPath": "/tmp"},
        ]
        container["volumeMounts"] = volume_mounts

        if not disable_nsjail:
            container["securityContext"] = {
                "privileged": True,
                "capabilities": {"add": ["SYS_ADMIN"]},
                "seccompProfile": {"type": "Unconfined"},
            }

    # Build pod security context
    pod_security_context: dict[str, Any] = {}
    if worker_type == "executor":
        sandbox = worker_spec.get("sandbox", {})
        if not sandbox.get("disableNsjail", False):
            pod_security_context = {"fsGroup": 0, "runAsUser": 0}
        else:
            pod_security_context = {"fsGroup": 1001, "runAsUser": 1001}
    else:
        pod_security_context = {"fsGroup": 1001, "runAsUser": 1001}

    # Build pod spec
    pod_spec: dict[str, Any] = {
        "securityContext": pod_security_context,
        "containers": [container],
    }

    if volumes:
        pod_spec["volumes"] = volumes

    # Service account
    service_account = spec.get("serviceAccount", {})
    if service_account.get("name"):
        pod_spec["serviceAccountName"] = service_account["name"]

    # Add network labels for postgres/redis access
    pod_labels = selector_labels.copy()
    pod_labels["tracecat.com/access-postgres"] = "true"
    pod_labels["tracecat.com/access-redis"] = "true"

    deployment: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": deployment_name,
            "labels": labels,
        },
        "spec": {
            "replicas": worker_spec.get("replicas", 1),
            "selector": {"matchLabels": selector_labels},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": pod_spec,
            },
        },
    }

    return deployment


def build_scaled_object(
    pool_name: str,
    worker_type: str,
    worker_spec: dict[str, Any],
    autoscaling_spec: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Build a KEDA ScaledObject for a worker type."""
    if not autoscaling_spec.get("enabled", False):
        return None

    worker_autoscaling = autoscaling_spec.get("workers", {}).get(worker_type, {})
    if not worker_autoscaling:
        return None

    deployment_name = f"{pool_name}-{worker_type}-worker"
    scaled_object_name = f"{pool_name}-{worker_type}-scaler"

    labels = build_labels(pool_name, worker_type)

    # Get Temporal config for the trigger
    temporal = spec.get("temporal", {})
    temporal_url = temporal.get("clusterUrl", "")
    temporal_namespace = temporal.get("namespace", "default")
    queue_name = worker_spec.get("queue", "")

    min_replicas = worker_autoscaling.get("minReplicas", 1)
    max_replicas = worker_autoscaling.get("maxReplicas", 10)
    target_queue_size = worker_autoscaling.get("targetQueueSize", 10)

    # Build trigger metadata for Temporal
    trigger_metadata: dict[str, str] = {
        "serverAddress": temporal_url,
        "namespace": temporal_namespace,
        "taskQueue": queue_name,
        "targetQueueSize": str(target_queue_size),
    }

    # Build authentication if API key is used
    trigger_auth = {}
    auth = temporal.get("auth", {})
    api_key_ref = auth.get("apiKeySecretRef")
    if api_key_ref:
        trigger_auth = {
            "name": f"{pool_name}-temporal-auth",
            "spec": {
                "secretTargetRef": [
                    {
                        "parameter": "apiKey",
                        "name": api_key_ref["name"],
                        "key": api_key_ref.get("key", "apiKey"),
                    }
                ]
            },
        }

    triggers = [
        {
            "type": "temporal",
            "metadata": trigger_metadata,
        }
    ]

    if trigger_auth:
        triggers[0]["authenticationRef"] = {"name": trigger_auth["name"]}

    scaled_object: dict[str, Any] = {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {
            "name": scaled_object_name,
            "labels": labels,
        },
        "spec": {
            "scaleTargetRef": {
                "name": deployment_name,
            },
            "minReplicaCount": min_replicas,
            "maxReplicaCount": max_replicas,
            "pollingInterval": 30,
            "cooldownPeriod": 300,
            "triggers": triggers,
        },
    }

    return scaled_object, trigger_auth if trigger_auth else None


def build_trigger_authentication(
    pool_name: str,
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a KEDA TriggerAuthentication for Temporal API key."""
    temporal = spec.get("temporal", {})
    auth = temporal.get("auth", {})
    api_key_ref = auth.get("apiKeySecretRef")

    if not api_key_ref:
        return None

    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "TriggerAuthentication",
        "metadata": {
            "name": f"{pool_name}-temporal-auth",
            "labels": build_labels(pool_name, "auth"),
        },
        "spec": {
            "secretTargetRef": [
                {
                    "parameter": "apiKey",
                    "name": api_key_ref["name"],
                    "key": api_key_ref.get("key", "apiKey"),
                }
            ]
        },
    }
