"""EE Git workflow store shim."""

try:
    from tracecat_ee.workflows.git_store import GitWorkflowStore  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "Tracecat Enterprise features are not installed. Install with extras: "
        'pip install "tracecat[ee]".'
    ) from exc

__all__ = ["GitWorkflowStore"]
