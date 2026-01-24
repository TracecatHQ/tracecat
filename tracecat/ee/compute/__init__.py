"""Enterprise Edition compute module for worker pool management."""

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

# Conditional import of WorkerPoolManager from EE package
try:
    from tracecat_ee.compute.manager import (
        WorkerPoolManager as WorkerPoolManager,
    )
except ImportError:
    from typing import NoReturn

    class _WorkerPoolManagerStub:
        """Stub for WorkerPoolManager when EE is not installed."""

        def __init__(self, *_args: object, **_kwargs: object) -> NoReturn:
            msg = (
                "WorkerPoolManager requires the Enterprise Edition. "
                "Install with: pip install tracecat[ee]"
            )
            raise NotImplementedError(msg)

    WorkerPoolManager = _WorkerPoolManagerStub  # type: ignore[misc,assignment]
