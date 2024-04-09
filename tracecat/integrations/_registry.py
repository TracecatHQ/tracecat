import inspect
from typing import Any, Self

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
    _integrations: dict[str, FunctionType] = {}
    _metadata: dict[str, dict[str, Any]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __contains__(self, name: str) -> bool:
        return name in self._integrations

    def __getitem__(self, name: str) -> FunctionType:
        return self.get_integration(name)

    @classmethod
    def register(cls, description: str, **integration_kwargs):
        """Decorator factory to register a new integration function with additional parameters."""

        def decorator_register(func: FunctionType):
            validate_type_constraints(func)
            platform = get_integration_platform(func)
            key = get_integration_key(func)

            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            if key in cls._integrations:
                raise ValueError(f"Integration '{key}' is already registered.")
            if not callable(func):
                raise ValueError("Provided object is not a callable function.")
            # Store function and decorator arguments in a dict
            cls._integrations[key] = func
            cls._metadata[key] = {
                "platform": platform,
                "description": description,
                "return_type": str(func.__annotations__.get("return")),
                **integration_kwargs,
            }
            return wrapper

        return decorator_register

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

    def _get_spec(self, key: str) -> IntegrationSpec:
        func = self.integrations[key]

        # Inspecting function arguments
        params = inspect.signature(func).parameters
        param_list = [param_to_spec(name, param) for name, param in params.items()]
        platform = get_integration_platform(func)

        metadata = self.metadata[key]
        return IntegrationSpec(
            name=func.__name__,
            description=metadata["description"],
            docstring=func.__doc__ or "No documentation provided.",
            platform=platform,
            parameters=param_list,
        )

    def get_registered_integration_specs(self) -> list[IntegrationSpec]:
        """Convert the registry to a dictionary of integration functions."""

        return [self._get_spec(key) for key in self.integrations]


registry = Registry()
