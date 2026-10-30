"""Tracecat managed actions and integrations registry."""

__version__ = "1.3.1"
__pep440_version__ = "1.3.1"

from typing import TYPE_CHECKING

from tracecat_registry import types
from tracecat_registry import ctx as ctx
from tracecat_registry._internal import exceptions, registry, secrets
from tracecat_registry._internal.exceptions import (
    ActionIsInterfaceError,
    RegistryActionError,
    SecretNotFoundError,
)
from tracecat_registry._internal.logger import logger

if TYPE_CHECKING:
    from tracecat_registry._internal.models import (
        RegistryOAuthSecret,
        RegistrySecret,
        RegistrySecretType,
        RegistrySecretTypeValidator,
    )

# Building the secret models costs ~75 ms of pydantic schema work. Every
# sandboxed action imports this package at startup, most without declaring
# secrets, so load the models on first access instead.
_LAZY_MODEL_NAMES = frozenset(
    {
        "RegistryOAuthSecret",
        "RegistrySecret",
        "RegistrySecretType",
        "RegistrySecretTypeValidator",
    }
)


def __getattr__(name: str) -> object:
    if name in _LAZY_MODEL_NAMES:
        from tracecat_registry._internal import models

        # getattr: the name is only known at runtime (PEP 562 lazy export).
        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "registry",
    "ctx",
    "types",
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
