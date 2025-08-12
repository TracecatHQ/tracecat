"""EE workflows shim module."""

try:
    from tracecat_ee.store import (  # noqa: F401
        GitWorkflowStore,
        sync_repo_workflows,
    )
except ImportError as exc:
    raise ImportError(
        "Tracecat Enterprise features are not installed. Install with extras: "
        'pip install "tracecat[ee]".'
    ) from exc

__all__ = ["GitWorkflowStore", "sync_repo_workflows"]
