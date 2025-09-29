import functools
import importlib.metadata
import uuid
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from tracecat import config
from tracecat.logger import logger

P = ParamSpec("P")
T = TypeVar("T")
ImplT = Callable[P, T] | type[T]


@functools.lru_cache(maxsize=128)
def load_ee_impl[T: ImplT](group: str, *, default: T) -> T:
    """Load the EE implementation of a plugin group."""
    if not config.ENTERPRISE_EDITION:
        logger.debug(f"Loading {group} implementation from {default}")
        return default
    entry_points = importlib.metadata.entry_points(group=group)
    impl = next((ep.load() for ep in entry_points), None)
    if impl is None:
        logger.warning(f"No {group} implementation found, using default")
        return default
    logger.debug(f"Loaded {group} implementation from {impl}")
    return impl


def is_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False
