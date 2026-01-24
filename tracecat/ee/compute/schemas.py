"""Dataclass schemas for TracecatWorkerPool CRD specifications."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TenantType(str, Enum):
    """Type of tenant for a worker pool."""

    SHARED = "shared"
    DEDICATED = "dedicated"


class Tier(str, Enum):
    """Subscription tier."""

    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class ResourceRequirements:
    """Kubernetes resource requirements."""

    cpu: str
    memory: str


@dataclass
class Resources:
    """Kubernetes resources specification."""

    requests: ResourceRequirements
    limits: ResourceRequirements


@dataclass
class ContextCompressionSpec:
    """Context compression configuration."""

    enabled: bool = False
    thresholdKb: int = 16


@dataclass
class SandboxSpec:
    """Sandbox configuration for executor."""

    disableNsjail: bool = False


@dataclass
class WorkerSpec:
    """Worker specification."""

    replicas: int
    queue: str
    resources: Resources
    enabled: bool = True
    # DSL worker specific
    threadpoolMaxWorkers: int | None = None
    contextCompression: ContextCompressionSpec | None = None
    # Executor worker specific
    backend: str | None = None
    workerPoolSize: int | None = None
    sandbox: SandboxSpec | None = None
    # Agent/Executor worker specific
    maxConcurrentActivities: int | None = None


@dataclass
class WorkersSpec:
    """Workers configuration for a worker pool."""

    dsl: WorkerSpec
    executor: WorkerSpec
    agent: WorkerSpec | None = None


@dataclass
class TenantSpec:
    """Tenant specification for a worker pool."""

    type: TenantType
    organizationId: str | None = None


@dataclass
class AutoscalingWorkerConfig:
    """Autoscaling configuration for a worker type."""

    minReplicas: int = 1
    maxReplicas: int = 10
    targetQueueSize: int = 10


@dataclass
class AutoscalingSpec:
    """KEDA autoscaling specification."""

    enabled: bool = False
    workers: dict[str, AutoscalingWorkerConfig] = field(default_factory=dict)


@dataclass
class ImageSpec:
    """Container image specification."""

    repository: str = "ghcr.io/tracecathq/tracecat"
    tag: str = "latest"
    pullPolicy: str = "IfNotPresent"


@dataclass
class SecretRef:
    """Reference to a Kubernetes secret."""

    name: str
    key: str = "apiKey"


@dataclass
class TemporalAuthSpec:
    """Temporal authentication specification."""

    apiKeySecretRef: SecretRef | None = None


@dataclass
class TemporalSpec:
    """Temporal cluster specification."""

    clusterUrl: str = "temporal-frontend:7233"
    namespace: str = "default"
    auth: TemporalAuthSpec | None = None


@dataclass
class DatabaseCredentialsRef:
    """Reference to database credentials secret."""

    name: str
    usernameKey: str = "username"
    passwordKey: str = "password"


@dataclass
class DatabaseSpec:
    """Database specification."""

    host: str = "postgres-rw"
    port: int = 5432
    database: str = "app"
    sslMode: str = "disable"
    credentialsSecretRef: DatabaseCredentialsRef | None = None


@dataclass
class RedisSpec:
    """Redis specification."""

    url: str | None = None
    urlSecretRef: SecretRef | None = None


@dataclass
class CoreSecretRef:
    """Reference to core secrets."""

    name: str


@dataclass
class SecretsSpec:
    """Secrets specification."""

    coreSecretRef: CoreSecretRef | None = None


@dataclass
class ServiceAccountSpec:
    """Service account specification."""

    name: str | None = None


@dataclass
class WorkerPoolSpec:
    """Full specification for a TracecatWorkerPool CR."""

    tenant: TenantSpec
    workers: WorkersSpec
    image: ImageSpec | None = None
    temporal: TemporalSpec | None = None
    database: DatabaseSpec | None = None
    redis: RedisSpec | None = None
    secrets: SecretsSpec | None = None
    serviceAccount: ServiceAccountSpec | None = None
    autoscaling: AutoscalingSpec | None = None

    def to_dict(self, exclude_none: bool = True) -> dict[str, Any]:
        """Convert to dictionary, optionally excluding None values."""

        def _convert_value(v: Any) -> Any:
            if v is None:
                return None
            if isinstance(v, Enum):
                return v.value
            if isinstance(v, dict):
                result = {}
                for dk, dv in v.items():
                    converted = _convert_value(dv)
                    if not exclude_none or converted is not None:
                        result[dk] = converted
                return result if result else None if exclude_none else {}
            if hasattr(v, "__dataclass_fields__"):
                result = {}
                for field_name in v.__dataclass_fields__:
                    field_val = getattr(v, field_name)
                    converted = _convert_value(field_val)
                    if not exclude_none or converted is not None:
                        result[field_name] = converted
                return result if result else None if exclude_none else {}
            return v

        result = {}
        for field_name in self.__dataclass_fields__:
            field_val = getattr(self, field_name)
            converted = _convert_value(field_val)
            if not exclude_none or converted is not None:
                result[field_name] = converted
        return result
