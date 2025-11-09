from collections.abc import Callable, Mapping
from typing import Any


class Unset:
    """Sentinel value for indicating that a value is not set."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<Unset>"


UNSET = Unset()
"""Sentinel value for indicating that a value is not set as `None` is a valid value."""


def is_iterable(value: object, *, container_only: bool = True) -> bool:
    """Check if a value is iterable, optionally excluding string-like and mapping types.

    Args:
        value: The value to check for iterability
        container_only: If True, excludes strings and bytes objects from being considered iterable

    Returns:
        bool: True if the value is iterable (according to the specified rules), False otherwise
    """
    if isinstance(value, str | bytes):
        return not container_only
    if isinstance(value, Mapping):
        return False
    return hasattr(value, "__iter__")


def all_activities(obj: object) -> list[Callable[..., Any]]:
    """Get all Temporal activities in an object."""
    return [
        fn
        for method_name in dir(obj)
        if hasattr(
            fn := getattr(obj, method_name),
            "__temporal_activity_definition",
        )
        and callable(fn)
    ]
