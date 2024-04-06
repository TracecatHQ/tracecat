from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from tracecat.integrations.another_integration import integration_1
from tracecat.integrations.experimental import (
    experimental_integration,
    experimental_integration_v2,
)

INTEGRATION_FACTORY: dict[str, Callable[..., Any]] = {
    "integrations.experimental.experimental_integration": experimental_integration,
    "integrations.experimental.experimental_integration_v2": experimental_integration_v2,
    "integrations.another_integration.integration_1": integration_1,
}


class IntegrationSpec(BaseModel):
    name: str
    description: str  # Possibly redundant
    docstring: str
    platform: str  # e.g. AWS, Google Workspace, Crowdstrike
    parameters: list[_ParameterSpec]


class _ParameterSpec(BaseModel):
    name: str
    type: str
    default: str | None = None


def function_to_spec(func: Callable[..., Any]) -> IntegrationSpec:
    """
    Translates a Python function into a JSON specification,
    including its name, parameters, and docstring.
    """
    if not callable(func):
        raise ValueError("Provided object is not a callable function.")
    if not hasattr(func, "__integration_metadata__"):
        raise ValueError("Provided function is not a Tracecat integration.")

    # Inspecting function arguments
    params = inspect.signature(func).parameters
    param_list = [
        _ParameterSpec(
            name=name,
            type=param.annotation.__name__,
            default=param.default if param.default != inspect.Parameter.empty else None,
        )
        for name, param in params.items()
    ]

    *_, platform = func.__module__.split(".")
    return IntegrationSpec(
        name=func.__name__,
        description=func.__integration_metadata__["description"],
        docstring=func.__doc__ or "No documentation provided.",
        platform=platform,
        parameters=param_list,
    )
