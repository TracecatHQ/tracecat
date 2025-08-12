"""EE Git workflow synchronization shim."""

try:
    from tracecat_ee.store.git_sync import sync_repo_workflows  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "Tracecat Enterprise features are not installed. Install with extras: "
        'pip install "tracecat[ee]".'
    ) from exc

__all__ = ["sync_repo_workflows"]
