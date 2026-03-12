"""Executor readiness healthcheck entry point.

Intended to be invoked as a Kubernetes readiness probe via:

    python -m tracecat.executor.healthcheck

Exits 0 when the warm-cache ready file exists, 1 otherwise.
"""

from __future__ import annotations

import sys

from tracecat.executor.warm_readiness import get_warm_ready_file, is_warm_ready
from tracecat.logger import logger


def main() -> int:
    """Return 0 if the executor warm-cache readiness marker exists, 1 otherwise."""
    if is_warm_ready():
        return 0
    logger.info(
        "Executor warmup readiness gate not yet satisfied",
        ready_file=str(get_warm_ready_file()),
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
