import functools
import importlib.metadata
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from tracecat import config

P = ParamSpec("P")
T = TypeVar("T")
ImplT = Callable[P, T] | type[T]


@functools.lru_cache(maxsize=128)
def load_ee_impl[T: ImplT](group: str, *, default: T) -> T:
    """Load the EE implementation of a plugin group."""
    if not config.ENTERPRISE_EDITION:
        return default
    entry_points = importlib.metadata.entry_points(group=group)
    return next((ep.load() for ep in entry_points), default)
