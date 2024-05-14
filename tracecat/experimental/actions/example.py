# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, TypedDict

from typing_extensions import Doc

from tracecat.experimental.registry import registry


class Stats(TypedDict):
    strength: int
    dexterity: int
    intelligence: int
    vitality: int


@registry.register(
    description="This is a test function",
    namespace="test",
    version="0.1.0",
    secrets=["test_secret"],
)
def my_function(
    age: Annotated[int, Doc("Persons age in years")] = 30,
    name: Annotated[str, Doc("Name of the person")] = "John Doe",
    is_member: bool = False,
) -> Stats:
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


@registry.register(
    description="This is another test function",
    namespace="test",
    version="0.1.0",
    secrets=["test_secret", "test_secret2"],
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
