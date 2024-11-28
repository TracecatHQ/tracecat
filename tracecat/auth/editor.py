import inspect
from typing import Union, get_type_hints

from fastapi import APIRouter
from pydantic import BaseModel

from tracecat.expressions.functions import FUNCTION_MAPPING

router = APIRouter(prefix="/editor", tags=["editor"])


class ParameterMeta(BaseModel):
    name: str
    type: str
    optional: bool


class FunctionMeta(BaseModel):
    name: str
    description: str
    parameters: list[ParameterMeta]
    return_type: str


@router.get("/functions")
async def get_functions() -> list[FunctionMeta]:
    functions = []

    for name, func in FUNCTION_MAPPING.items():
        # Get function signature
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        type_hints = get_type_hints(func)

        # Extract parameter information
        parameters = []
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            param_type_hint = type_hints.get(param_name, "Any")

            def format_type(type_hint) -> str:
                if hasattr(type_hint, "__origin__") and type_hint.__origin__ is Union:
                    return " | ".join(format_type(t) for t in type_hint.__args__)
                # Handle generic types like list[str], dict[str, int], etc.
                elif hasattr(type_hint, "__origin__"):
                    args = ", ".join(format_type(arg) for arg in type_hint.__args__)
                    return f"{type_hint.__origin__.__name__}[{args}]"
                # Handle basic types
                return getattr(type_hint, "__name__", str(type_hint))

            param_type = format_type(param_type_hint)
            parameters.append(
                ParameterMeta(
                    name=param_name,
                    type=param_type,
                    optional=param.default != inspect.Parameter.empty,
                )
            )

        # Update return type handling
        return_type = type_hints.get("return", "Any")
        return_type_str = format_type(return_type)

        functions.append(
            FunctionMeta(
                name=name,
                description=doc,
                parameters=parameters,
                return_type=return_type_str,
            )
        )

    return functions
