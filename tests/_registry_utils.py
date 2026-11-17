"""Helpers for inspecting registry UDF schemas in tests.

These import UDF modules normally instead of calling ``Repository.init()``, which
reloads them and breaks ``pytest.raises`` on their exception classes in other tests.
"""

import functools
import importlib
import pkgutil
from collections.abc import Callable
from typing import Any

import tracecat_registry.core

from tracecat.registry.repository import RegisterKwargs, generate_model_from_function


def input_schema_for(fn: Callable[..., Any]) -> dict[str, Any]:
    kwargs = RegisterKwargs.model_validate(getattr(fn, "__tracecat_udf_kwargs"))
    args_cls, _, _ = generate_model_from_function(fn, kwargs)
    return args_cls.model_json_schema()


@functools.cache
def core_udfs() -> dict[str, Callable[..., Any]]:
    """Map each built-in UDF's action name to its function."""
    core = tracecat_registry.core
    udfs: dict[str, Callable[..., Any]] = {}
    for info in pkgutil.walk_packages(core.__path__, f"{core.__name__}."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            kwargs = getattr(obj, "__tracecat_udf_kwargs", None)
            if isinstance(kwargs, dict) and obj.__module__ == module.__name__:
                udfs[f"{kwargs['namespace']}.{obj.__name__}"] = obj
    return udfs
