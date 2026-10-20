"""Process-wide record that this worker has begun shutting down."""

_shutting_down = False


def mark_worker_shutting_down() -> None:
    """Record that the worker received a shutdown signal."""
    global _shutting_down
    _shutting_down = True


def worker_shutting_down() -> bool:
    """Whether the worker received a shutdown signal."""
    return _shutting_down
