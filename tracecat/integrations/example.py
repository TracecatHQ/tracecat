"""Demo integrations.

Note
----
- These integrations are used for demo purposes.
- You should not use these integrations in production.
"""

import os
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
    description="Demonstrates joining strings together",
)
def join(strings: list[str], delimiter: str = ",") -> str:
    """Joins 2 strings together."""
    return delimiter.join(strings)


@registry.register(
    description="This function has many complex parameters",
)
def complex_example(
    y: bool,
    my_list: list[str],
    my_dict: dict[str, str],
    my_enum: Literal["a", "b", "c"],
    optional_int: int | None = None,
    x: str | None = None,
) -> int:
    """This function has many complex parameters."""
    return 1


@registry.register(
    description="Test description",
    secrets=["test_secret"],  # test_secret.TEST_KEY
)
def secretive_add(nums: list[int]) -> int:
    """Adds integers together."""
    value = os.environ["TEST_KEY"]
    print(f"VALUE: {value}")
    return sum(nums)
