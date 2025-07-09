"""Tracecat managed actions and integrations registry."""

__version__ = "0.1.0"


try:
    import tracecat  # noqa: F401
except ImportError:
    raise ImportError(
        "Could not import tracecat. Please install `tracecat` to use the registry."
    ) from None

from tracecat_registry._internal import exceptions, registry, secrets
from tracecat_registry._internal.exceptions import (
    ActionIsInterfaceError,
    RegistryActionError,
    SecretNotFoundError,
)
from tracecat_registry._internal.logger import logger
from tracecat_registry._internal.models import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretType,
    RegistrySecretTypeValidator,
)

__all__ = [
    "registry",
    "RegistrySecret",
    "logger",
    "RegistryOAuthSecret",
    "RegistrySecretType",
    "RegistrySecretTypeValidator",
    "secrets",
    "exceptions",
    "RegistryActionError",
    "ActionIsInterfaceError",
    "SecretNotFoundError",
]
