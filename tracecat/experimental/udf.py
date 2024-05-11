from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, ParamSpec, TypedDict, TypeVar

from typing_extensions import Doc


class Stats(TypedDict):
    strength: int
    dexterity: int
    intelligence: int
    vitality: int


P = ParamSpec("P")
R = TypeVar("R")


def experimental(func: Callable[P, R]) -> Callable[P, R]:
    def decorator(*args: Any, **kwargs: Any) -> R:
        print("This is an experimental function")
        return func(*args, **kwargs)

    return decorator


# @experimental
def my_function(
    age: Annotated[int, Doc("Persons age in years")] = 30,
    name: Annotated[str, Doc("Name of the person")] = "John Doe",
    is_member: bool = False,
) -> Stats:
    # Function implementation goes here
    print(f"Name: {name}, Age: {age}, Is member: {is_member}")
    return {"strength": 10, "dexterity": 20, "intelligence": 30, "vitality": 40}
