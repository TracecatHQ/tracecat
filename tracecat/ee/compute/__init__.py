"""Enterprise Edition compute module for worker pool management."""

from tracecat.ee.compute.manager import WorkerPoolManager
from tracecat.ee.compute.schemas import (
    AutoscalingSpec,
    AutoscalingWorkerConfig,
    ResourceRequirements,
    Resources,
    TenantSpec,
    TenantType,
    Tier,
    WorkerPoolSpec,
    WorkerSpec,
    WorkersSpec,
)

__all__ = [
    "AutoscalingSpec",
    "AutoscalingWorkerConfig",
    "ResourceRequirements",
    "Resources",
    "TenantSpec",
    "TenantType",
    "Tier",
    "WorkerPoolManager",
    "WorkerPoolSpec",
    "WorkerSpec",
    "WorkersSpec",
]
