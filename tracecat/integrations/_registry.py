from __future__ import annotations

import asyncio
import inspect
import os
from typing import TYPE_CHECKING, Any, Self

from tracecat import secrets as ts
from tracecat.auth import Role
from tracecat.integrations._meta import (
    IntegrationSpec,
    param_to_spec,
    validate_type_constraints,
)
from tracecat.integrations.utils import (
    FunctionType,
    get_integration_key,
    get_integration_platform,
)
from tracecat.logger import standard_logger

if TYPE_CHECKING:
    from tracecat.db import Secret

logger = standard_logger(__name__)


class Registry:
    """Singleton class to store and manage all registered integrations.

    Note
    ----
    - The registry is a singleton class that stores all registered integrations.
    - We only have well-defined support for simple builtin types
    - Currently, creating integrations with complex union types will lead to undefined behavor
    """

    _instance: Self = None
    _integrations: dict[str, FunctionType]
    _metadata: dict[str, dict[str, Any]]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._integrations = {}
            cls._metadata = {}
        return cls._instance

    def __contains__(self, name: str) -> bool:
        return name in self._integrations

    def __getitem__(self, name: str) -> FunctionType:
        return self.get_integration(name)

    def register(
        self,
        description: str,
        secrets: list[str] | None = None,
        **integration_kwargs,
    ):
        """Decorator factory to register a new integration function with additional parameters."""

        def decorator_register(func: FunctionType):
            logger.info(f"Registering integration {func.__name__}")
            validate_type_constraints(func)
            platform = get_integration_platform(func)
            key = get_integration_key(func)

            def wrapper(*args, **kwargs):
                """Wrapper function for the integration.

                Responsibilities
                ----------------
                Before invoking the function:
                1. Grab all the secrets from the secrets API.
                2. Inject all secret keys into the execution environment.
                3. Clean up the environment after the function has executed.
                """
                _secrets: list[Secret] = []
                try:
                    role = kwargs.pop("__role", None)
                    # Get secrets from the secrets API
                    self._logger = standard_logger(f"{__name__}[PID={os.getpid()}]")
                    self._logger.info("Executing %r in subprocess", key)

                    if secrets:
                        self._logger.info(f"Pulling secrets: {secrets!r}")
                        _secrets = self._get_secrets(role=role, secret_names=secrets)
                        self._set_secrets(_secrets)

                    return func(*args, **kwargs)
                except Exception as e:
                    self._logger.error(f"Error running integration '{key}': {e}")
                    raise
                finally:
                    self._logger.info(f"Cleaning up after integration '{key}'.")
                    self._unset_secrets(_secrets)

            if key in self._integrations:
                raise ValueError(f"Integration '{key}' is already registered.")
            if not callable(func):
                raise ValueError("Provided object is not a callable function.")
            # Store function and decorator arguments in a dict
            self._integrations[key] = wrapper
            self._metadata[key] = {
                "platform": platform,
                "description": description,
                "return_type": str(func.__annotations__.get("return")),
                "specification": self._create_spec(func=func, description=description),
                **integration_kwargs,
            }
            return wrapper

        return decorator_register

    def _get_secrets(self, role: Role, secret_names: list[str]) -> list[Secret]:
        """Retrieve secrets from the secrets API."""

        self._logger.info(f"Getting secrets {secret_names!r}")
        return asyncio.run(ts.batch_get_secrets(role, secret_names))

    def _set_secrets(self, secrets: list[Secret]):
        """Set secrets in the environment."""
        for secret in secrets:
            self._logger.info(f"Setting secret {secret!r}")
            for kv in secret.keys:
                os.environ[kv.key] = kv.value

    def _unset_secrets(self, secrets: list[Secret]):
        for secret in secrets:
            self._logger.info(f"Deleting secret {secret.name!r}")
            for kv in secret.keys:
                del os.environ[kv.key]

    def _create_spec(
        self, *, func: FunctionType, description: str | None
    ) -> IntegrationSpec:
        """Get the specification for a registered integration function."""
        # Inspecting function arguments
        params = inspect.signature(func).parameters
        param_list = [param_to_spec(name, param) for name, param in params.items()]
        platform = get_integration_platform(func)

        return IntegrationSpec(
            name=func.__name__,
            description=description,
            docstring=func.__doc__ or "No documentation provided.",
            platform=platform,
            parameters=param_list,
        )

    @property
    def metadata(self) -> dict[str, dict[str, Any]]:
        """Return metadata for all registered integrations."""
        return self._metadata

    @property
    def integrations(self) -> dict[str, FunctionType]:
        """Return all registered integrations."""
        return self._integrations

    def get_integration(self, name: str) -> FunctionType:
        """Retrieve a registered integration function."""
        if name not in self._integrations:
            raise ValueError(f"Integration '{name}' not found.")
        return self._integrations[name]

    def list_integrations(self) -> list:
        """List all registered integrations."""
        return list(self._integrations.keys())

    def get_registered_integration_specs(self) -> list[IntegrationSpec]:
        """Convert the registry to a dictionary of integration functions."""
        return [self.metadata[key]["specification"] for key in self.integrations]


registry = Registry()
