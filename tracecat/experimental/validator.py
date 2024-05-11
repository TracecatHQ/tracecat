import inspect
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from pydantic import BaseModel, create_model

from tracecat.experimental.udf import my_function

P = ParamSpec("P")
R = TypeVar("R")


def generate_model_from_function(
    func: Callable[P, R],
) -> tuple[type[BaseModel], Any | None]:
    # Get the signature of the function
    sig = inspect.signature(func)
    # Create a dictionary to hold field definitions
    fields = {}
    for name, param in sig.parameters.items():
        # Use the annotation and default value of the parameter to define the model field
        field_type = param.annotation
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (field_type, default)
    # Dynamically create and return the Pydantic model class
    input_model = create_model(f"{func.__name__}Model", **fields)
    # Capture the return type of the function
    return_type = (
        sig.return_annotation
        if sig.return_annotation is not inspect.Signature.empty
        else None
    )

    return input_model, return_type


# Generate the model
if __name__ == "__main__":
    my_function()
    InputModel, OutputTupe = generate_model_from_function(my_function)
    print(InputModel.model_fields)
    print(OutputTupe)
