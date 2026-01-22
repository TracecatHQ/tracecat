"""Manager for creating and managing TracecatWorkerPool CRs."""

from __future__ import annotations

import logging
from typing import ClassVar, TypedDict, Unpack

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from tracecat.ee.compute.schemas import (
    AutoscalingSpec,
    AutoscalingWorkerConfig,
    ContextCompressionSpec,
    CoreSecretRef,
    DatabaseCredentialsRef,
    DatabaseSpec,
    ImageSpec,
    RedisSpec,
    ResourceRequirements,
    Resources,
    SandboxSpec,
    SecretRef,
    SecretsSpec,
    ServiceAccountSpec,
    TemporalAuthSpec,
    TemporalSpec,
    TenantSpec,
    TenantType,
    Tier,
    WorkerPoolSpec,
    WorkerSpec,
    WorkersSpec,
)

logger = logging.getLogger(__name__)


class WorkerPoolCR(TypedDict, total=False):
    """TracecatWorkerPool custom resource structure."""

    apiVersion: str
    kind: str
    metadata: dict[str, object]
    spec: dict[str, object]
    status: dict[str, object]


class WorkerPoolConfigKwargs(TypedDict, total=False):
    """Optional configuration for worker pool provisioning."""

    image_tag: str
    temporal_url: str
    temporal_namespace: str
    temporal_api_key_secret: str | None
    database_host: str
    database_port: int
    database_name: str
    database_credentials_secret: str
    redis_url: str
    core_secrets_name: str
    service_account_name: str | None


# Default resources by tier and worker type
DEFAULT_RESOURCES: dict[Tier, dict[str, Resources]] = {
    Tier.STARTER: {
        "dsl": Resources(
            requests=ResourceRequirements(cpu="500m", memory="512Mi"),
            limits=ResourceRequirements(cpu="1000m", memory="1Gi"),
        ),
        "executor": Resources(
            requests=ResourceRequirements(cpu="500m", memory="512Mi"),
            limits=ResourceRequirements(cpu="1000m", memory="1Gi"),
        ),
    },
    Tier.PRO: {
        "dsl": Resources(
            requests=ResourceRequirements(cpu="1000m", memory="1Gi"),
            limits=ResourceRequirements(cpu="2000m", memory="2Gi"),
        ),
        "executor": Resources(
            requests=ResourceRequirements(cpu="1000m", memory="1Gi"),
            limits=ResourceRequirements(cpu="2000m", memory="2Gi"),
        ),
        "agent": Resources(
            requests=ResourceRequirements(cpu="1000m", memory="2Gi"),
            limits=ResourceRequirements(cpu="2000m", memory="4Gi"),
        ),
    },
    Tier.ENTERPRISE: {
        "dsl": Resources(
            requests=ResourceRequirements(cpu="1000m", memory="1Gi"),
            limits=ResourceRequirements(cpu="2000m", memory="2Gi"),
        ),
        "executor": Resources(
            requests=ResourceRequirements(cpu="1000m", memory="1Gi"),
            limits=ResourceRequirements(cpu="2000m", memory="2Gi"),
        ),
        "agent": Resources(
            requests=ResourceRequirements(cpu="2000m", memory="4Gi"),
            limits=ResourceRequirements(cpu="4000m", memory="8Gi"),
        ),
    },
}


class WorkerPoolManager:
    """Manager for TracecatWorkerPool custom resources.

    This class provides methods to create, update, and delete worker pools
    based on organization tier. It is used by the Tracecat API to provision
    compute resources when organizations change tiers.
    """

    GROUP: ClassVar[str] = "compute.tracecat.io"
    VERSION: ClassVar[str] = "v1alpha1"
    PLURAL: ClassVar[str] = "tracecatworkerpools"

    def __init__(self, in_cluster: bool = True) -> None:
        """Initialize the WorkerPoolManager.

        Args:
            in_cluster: If True, load in-cluster config. If False, load from kubeconfig.
        """
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        self.api: client.CustomObjectsApi = client.CustomObjectsApi()

    def get_pool_name(self, tier: Tier, org_id: str | None) -> str:
        """Get the pool name based on tier and org ID.

        Args:
            tier: The subscription tier.
            org_id: The organization ID (required for dedicated pools).

        Returns:
            The pool name.
        """
        if tier == Tier.ENTERPRISE and org_id:
            return org_id
        return f"{tier.value}-pool"

    def get_queue_prefix(self, tier: Tier, org_id: str | None) -> str:
        """Get the queue prefix based on tier and org ID.

        Args:
            tier: The subscription tier.
            org_id: The organization ID (required for dedicated pools).

        Returns:
            The queue prefix for naming task queues.
        """
        if tier == Tier.ENTERPRISE and org_id:
            return org_id
        return f"{tier.value}-pool"

    def get_spec_for_tier(
        self,
        tier: Tier,
        org_id: str | None,
        *,
        image_tag: str = "latest",
        temporal_url: str = "temporal-frontend:7233",
        temporal_namespace: str = "default",
        temporal_api_key_secret: str | None = None,
        database_host: str = "tracecat-postgres-rw",
        database_port: int = 5432,
        database_name: str = "app",
        database_credentials_secret: str = "tracecat-postgres-app",
        redis_url: str = "redis://tracecat-valkey:6379",
        core_secrets_name: str = "tracecat-secrets",
        service_account_name: str | None = "tracecat-app",
    ) -> WorkerPoolSpec:
        """Build a WorkerPoolSpec from tier and configuration.

        Args:
            tier: The subscription tier.
            org_id: The organization ID (required for dedicated/enterprise pools).
            image_tag: Docker image tag to use.
            temporal_url: Temporal cluster URL.
            temporal_namespace: Temporal namespace.
            temporal_api_key_secret: Name of secret containing Temporal API key.
            database_host: Database host.
            database_port: Database port.
            database_name: Database name.
            database_credentials_secret: Name of secret containing DB credentials.
            redis_url: Redis URL.
            core_secrets_name: Name of secret containing core Tracecat secrets.
            service_account_name: Name of ServiceAccount to use.

        Returns:
            A fully configured WorkerPoolSpec.
        """
        queue_prefix = self.get_queue_prefix(tier, org_id)
        resources = DEFAULT_RESOURCES[tier]

        # Build tenant spec
        if tier == Tier.ENTERPRISE:
            tenant = TenantSpec(type=TenantType.DEDICATED, organizationId=org_id)
        else:
            tenant = TenantSpec(type=TenantType.SHARED)

        # Build temporal auth if API key secret provided
        temporal_auth = None
        if temporal_api_key_secret:
            temporal_auth = TemporalAuthSpec(
                apiKeySecretRef=SecretRef(name=temporal_api_key_secret)
            )

        # Build workers spec based on tier
        if tier == Tier.STARTER:
            workers = WorkersSpec(
                dsl=WorkerSpec(
                    enabled=True,
                    replicas=2,
                    queue=f"{queue_prefix}-task-queue",
                    resources=resources["dsl"],
                    threadpoolMaxWorkers=50,
                    contextCompression=ContextCompressionSpec(enabled=False),
                ),
                executor=WorkerSpec(
                    enabled=True,
                    replicas=1,
                    queue=f"{queue_prefix}-action-queue",
                    resources=resources["executor"],
                    backend="auto",
                    sandbox=SandboxSpec(disableNsjail=False),
                    contextCompression=ContextCompressionSpec(enabled=False),
                ),
                agent=None,
            )
            autoscaling = AutoscalingSpec(enabled=False)
        elif tier == Tier.PRO:
            workers = WorkersSpec(
                dsl=WorkerSpec(
                    enabled=True,
                    replicas=3,
                    queue=f"{queue_prefix}-task-queue",
                    resources=resources["dsl"],
                    threadpoolMaxWorkers=75,
                    contextCompression=ContextCompressionSpec(enabled=False),
                ),
                executor=WorkerSpec(
                    enabled=True,
                    replicas=2,
                    queue=f"{queue_prefix}-action-queue",
                    resources=resources["executor"],
                    backend="pool",
                    workerPoolSize=5,
                    sandbox=SandboxSpec(disableNsjail=False),
                    contextCompression=ContextCompressionSpec(enabled=False),
                ),
                agent=WorkerSpec(
                    enabled=True,
                    replicas=1,
                    queue=f"{queue_prefix}-agent-queue",
                    resources=resources["agent"],
                    maxConcurrentActivities=25,
                ),
            )
            autoscaling = AutoscalingSpec(
                enabled=True,
                workers={
                    "dsl": AutoscalingWorkerConfig(
                        minReplicas=2, maxReplicas=10, targetQueueSize=10
                    ),
                    "executor": AutoscalingWorkerConfig(
                        minReplicas=1, maxReplicas=8, targetQueueSize=5
                    ),
                    "agent": AutoscalingWorkerConfig(
                        minReplicas=0, maxReplicas=5, targetQueueSize=3
                    ),
                },
            )
        else:  # ENTERPRISE
            workers = WorkersSpec(
                dsl=WorkerSpec(
                    enabled=True,
                    replicas=4,
                    queue=f"{queue_prefix}-task-queue",
                    resources=resources["dsl"],
                    threadpoolMaxWorkers=100,
                    contextCompression=ContextCompressionSpec(enabled=False),
                ),
                executor=WorkerSpec(
                    enabled=True,
                    replicas=2,
                    queue=f"{queue_prefix}-action-queue",
                    resources=resources["executor"],
                    backend="pool",
                    workerPoolSize=10,
                    sandbox=SandboxSpec(disableNsjail=False),
                    contextCompression=ContextCompressionSpec(enabled=False),
                ),
                agent=WorkerSpec(
                    enabled=True,
                    replicas=2,
                    queue=f"{queue_prefix}-agent-queue",
                    resources=resources["agent"],
                    maxConcurrentActivities=50,
                ),
            )
            autoscaling = AutoscalingSpec(
                enabled=True,
                workers={
                    "dsl": AutoscalingWorkerConfig(
                        minReplicas=2, maxReplicas=20, targetQueueSize=10
                    ),
                    "executor": AutoscalingWorkerConfig(
                        minReplicas=2, maxReplicas=15, targetQueueSize=5
                    ),
                    "agent": AutoscalingWorkerConfig(
                        minReplicas=1, maxReplicas=10, targetQueueSize=3
                    ),
                },
            )

        return WorkerPoolSpec(
            tenant=tenant,
            image=ImageSpec(tag=image_tag),
            temporal=TemporalSpec(
                clusterUrl=temporal_url,
                namespace=temporal_namespace,
                auth=temporal_auth,
            ),
            database=DatabaseSpec(
                host=database_host,
                port=database_port,
                database=database_name,
                credentialsSecretRef=DatabaseCredentialsRef(
                    name=database_credentials_secret
                ),
            ),
            redis=RedisSpec(url=redis_url),
            secrets=SecretsSpec(coreSecretRef=CoreSecretRef(name=core_secrets_name)),
            serviceAccount=ServiceAccountSpec(name=service_account_name)
            if service_account_name
            else None,
            workers=workers,
            autoscaling=autoscaling,
        )

    def _build_cr_body(
        self,
        name: str,
        namespace: str,
        tier: Tier,
        spec: WorkerPoolSpec,
    ) -> WorkerPoolCR:
        """Build the CR body for a worker pool.

        Args:
            name: The CR name.
            namespace: The Kubernetes namespace.
            tier: The subscription tier.
            spec: The worker pool specification.

        Returns:
            The CR body as a dictionary.
        """
        return {
            "apiVersion": f"{self.GROUP}/{self.VERSION}",
            "kind": "TracecatWorkerPool",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {
                    "tracecat.io/tier": tier.value,
                    "tracecat.io/tenant-type": spec.tenant.type.value,
                },
            },
            "spec": spec.to_dict(exclude_none=True),
        }

    async def provision_worker_pool(
        self,
        org_id: str,
        tier: Tier,
        namespace: str = "tracecat",
        **config_kwargs: Unpack[WorkerPoolConfigKwargs],
    ) -> WorkerPoolCR:
        """Create or update a worker pool for an organization.

        Args:
            org_id: The organization ID.
            tier: The subscription tier.
            namespace: The Kubernetes namespace.
            **config_kwargs: Additional configuration passed to get_spec_for_tier.

        Returns:
            The created/updated CR.
        """
        spec = self.get_spec_for_tier(tier, org_id, **config_kwargs)
        name = self.get_pool_name(tier, org_id)
        body = self._build_cr_body(name, namespace, tier, spec)

        try:
            result = self.api.patch_namespaced_custom_object(
                group=self.GROUP,
                version=self.VERSION,
                namespace=namespace,
                plural=self.PLURAL,
                name=name,
                body=body,
            )
            logger.info(f"Updated worker pool {name} for tier {tier.value}")
            return result
        except ApiException as e:
            if e.status == 404:
                result = self.api.create_namespaced_custom_object(
                    group=self.GROUP,
                    version=self.VERSION,
                    namespace=namespace,
                    plural=self.PLURAL,
                    body=body,
                )
                logger.info(f"Created worker pool {name} for tier {tier.value}")
                return result
            raise

    async def deprovision_worker_pool(
        self,
        org_id: str,
        namespace: str = "tracecat",
    ) -> None:
        """Delete a dedicated worker pool when an org downgrades.

        Args:
            org_id: The organization ID.
            namespace: The Kubernetes namespace.
        """
        try:
            self.api.delete_namespaced_custom_object(
                group=self.GROUP,
                version=self.VERSION,
                namespace=namespace,
                plural=self.PLURAL,
                name=org_id,
            )
            logger.info(f"Deleted worker pool for org {org_id}")
        except ApiException as e:
            if e.status == 404:
                logger.warning(
                    f"Worker pool for org {org_id} not found, skipping delete"
                )
            else:
                raise

    async def get_worker_pool(
        self,
        name: str,
        namespace: str = "tracecat",
    ) -> WorkerPoolCR | None:
        """Get a worker pool by name.

        Args:
            name: The worker pool name.
            namespace: The Kubernetes namespace.

        Returns:
            The worker pool CR or None if not found.
        """
        try:
            return self.api.get_namespaced_custom_object(
                group=self.GROUP,
                version=self.VERSION,
                namespace=namespace,
                plural=self.PLURAL,
                name=name,
            )
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    async def list_worker_pools(
        self,
        namespace: str = "tracecat",
        tier: Tier | None = None,
        tenant_type: TenantType | None = None,
    ) -> list[WorkerPoolCR]:
        """List worker pools with optional filters.

        Args:
            namespace: The Kubernetes namespace.
            tier: Filter by tier.
            tenant_type: Filter by tenant type.

        Returns:
            List of worker pool CRs.
        """
        label_selector = []
        if tier:
            label_selector.append(f"tracecat.io/tier={tier.value}")
        if tenant_type:
            label_selector.append(f"tracecat.io/tenant-type={tenant_type.value}")

        result = self.api.list_namespaced_custom_object(
            group=self.GROUP,
            version=self.VERSION,
            namespace=namespace,
            plural=self.PLURAL,
            label_selector=",".join(label_selector) if label_selector else None,
        )
        return result.get("items", [])
