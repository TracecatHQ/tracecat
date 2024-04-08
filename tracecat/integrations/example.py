"""Demo integrations.

Note
----
- These integrations are used for demo purposes.
- You should not use these integrations in production.
"""

from typing import Literal

from tracecat.integrations._registry import registry


@registry.register(
    description="This is a very simple integration with 2 parameters",
)
def add(nums: list[int]) -> int:
    """Adds 2 integers together."""
    return sum(nums)


@registry.register(
    description="This is another simple integration with 2 parameters",
    version="2",
)
def subtract(a: int, b: int, c: int | None = None) -> int:
    """Subtracts 2 integers."""
    return a + b + c


@registry.register(
    description="This function has many complex parameters",
)
def complex_example(
    y: bool,
    # my_list: list[str | int],
    my_dict: dict[str, str],
    my_enum: Literal["a", "b", "c"],
    optional_int: int | None = None,
    x: int | float | None = None,
    tup: tuple[int | float] | None = None,  # Lacking support
    my_dict_default: dict[str, int] | None = "default",  # Lacking support
    my_nested_dict_default: dict[str, list[str]] | None = "default",  # Lacking support
) -> int:
    """This function has many complex parameters."""
    return 1
