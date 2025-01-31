"""Example module with annotated functions for testing the registry."""

# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, TypedDict

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import registry


class Member(TypedDict):
    name: str
    age: int
    is_member: bool


@registry.register(
    description="For testing the registry",
    namespace="example",
    include_in_schema=False,
)
def passthrough(
    value: Annotated[str, Field(..., description="The value to pass through")],
) -> str:
    return value


@registry.register(
    description="Adder example",
    namespace="example",
    include_in_schema=False,
)
def add(
    lhs: Annotated[int, Field(..., description="The first number")],
    rhs: Annotated[int, Field(..., description="The second number")],
) -> int:
    return lhs + rhs


@registry.register(
    description="This is a test function",
    namespace="example",
    include_in_schema=False,
)
def my_function(
    age: Annotated[int, Doc("Persons age in years")] = 30,
    name: Annotated[str | None, Doc("Name of the person")] = None,
    is_member: bool = False,
) -> Member:
    """My function

    Parameters
    ----------
    age : Annotated[int, Doc, optional
        the age, by default 30
    name : Annotated[str, Doc, optional
        the name, by default "John Doe"
    is_member : bool, optional
        the memberships, by default False

    Returns
    -------
    Stats
        the result
    """
    name = name or "John Doe"
    return Member(name=name, age=age, is_member=is_member)


@registry.register(
    description="This is another test function",
    namespace="example",
    include_in_schema=False,
)
def another_function(
    age: Annotated[int, Doc("Persons age in years")] = 30,
    name: Annotated[str, Doc("Name of the person")] = "John Doe",
    is_member: Annotated[bool, Doc("Is the person a member")] = False,
) -> dict[str, Any]:
    """My function

    Parameters
    ----------
    age : Annotated[int, Doc, optional
        the age, by default 30
    name : Annotated[str, Doc, optional
        the name, by default "John Doe"
    is_member : bool, optional
        the memberships, by default False

    Returns
    -------
    Stats
        the result
    """
    # Function implementation goes here
    print(f"Name: {name}, Age: {age}, Is member: {is_member}")
    return {"strength": 10, "dexterity": 20, "intelligence": 30, "vitality": 40}
