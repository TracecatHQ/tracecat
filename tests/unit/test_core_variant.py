from collections.abc import Iterator
from typing import Annotated, Literal

import pytest
from pydantic import Field, TypeAdapter

from tracecat.core.schemas import Variant


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


class Shape(Variant, discriminator="type"):
    pass


class Circle(Shape):
    type: Literal["circle"] = "circle"
    radius: float


class Square(Shape):
    type: Literal["square"] = "square"
    side: float


shapes: TypeAdapter[Circle | Square] = TypeAdapter(
    Annotated[Circle | Square, Field(discriminator="type")]
)


def test_a_defaulted_discriminator_dumps_under_exclude_unset() -> None:
    circle = Circle(radius=1)
    assert circle.model_dump(exclude_unset=True) == {"type": "circle", "radius": 1}
    assert shapes.validate_python(circle.model_dump(exclude_unset=True)) == circle


def test_a_payload_decoded_without_the_tag_still_dumps_it() -> None:
    square = Square.model_validate({"side": 2})
    assert square.model_dump(exclude_unset=True) == {"type": "square", "side": 2}


def test_a_variant_needs_a_discriminator() -> None:
    with pytest.raises(TypeError, match="needs a discriminator"):

        class Untagged(Variant):
            kind: Literal["x"] = "x"


def test_a_member_must_declare_the_discriminator_field() -> None:
    with pytest.raises(TypeError, match="no discriminator field 'type'"):

        class Typo(Shape):
            tpye: Literal["typo"] = "typo"
