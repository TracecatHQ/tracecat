from collections.abc import Callable
from typing import Any, ParamSpec

_P = ParamSpec("_P")
FunctionType = Callable[_P, Any]


def get_integration_platform(func: FunctionType) -> str:
    """Dynamically get the platform name from a function."""
    *_, platform = func.__module__.split(".")
    return platform


def get_integration_key(func: FunctionType) -> str:
    """Get the key for a registered integration function."""
    platform = get_integration_platform(func)
    return f"integrations.{platform}.{func.__name__}"
