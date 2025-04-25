from collections.abc import Mapping

UNSET = object()
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
