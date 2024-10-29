"""Tracecat managed actions and integrations registry."""

__version__ = "0.1.0"


try:
    import tracecat  # noqa: F401
except ImportError:
    raise ImportError(
        "Could not import tracecat. Please install `tracecat` to use the registry."
    ) from None

from tracecat_registry._internal import registry, secrets
from tracecat_registry._internal.exceptions import (  # noqa: E402
    RegistryActionError,
    RegistryValidationError,
)
from tracecat_registry._internal.logger import logger
from tracecat_registry._internal.models import RegistrySecret

__all__ = [
    "registry",
    "RegistrySecret",
    "logger",
    "secrets",
    "exceptions",
    "RegistryValidationError",
    "RegistryActionError",
]
