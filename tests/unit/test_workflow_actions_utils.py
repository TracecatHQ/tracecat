"""Unit tests for Pydantic utility functions."""

import datetime as dt
import uuid
from enum import Enum
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from tracecat.workflow.actions.utils import generate_zero_defaults


class Color(Enum):
    """Test enum."""

    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class NestedModel(BaseModel):
    """Nested Pydantic model for testing."""

    name: str
    value: int


class CircularModel(BaseModel):
    """Model with circular reference for testing."""

    name: str
    child: "CircularModel | None" = None


class TestGenerateZeroDefaults:
    """Tests for generate_zero_defaults function."""

    def test_simple_string(self):
        """Test generation of default for simple string field."""

        class Model(BaseModel):
            name: str

        result = generate_zero_defaults(Model)
        assert result == {"name": ""}

    def test_simple_int(self):
        """Test generation of default for simple int field."""

        class Model(BaseModel):
            age: int

        result = generate_zero_defaults(Model)
        assert result == {"age": 0}

    def test_simple_float(self):
        """Test generation of default for simple float field."""

        class Model(BaseModel):
            price: float

        result = generate_zero_defaults(Model)
        assert result == {"price": 0.0}

    def test_simple_bool(self):
        """Test generation of default for simple bool field."""

        class Model(BaseModel):
            active: bool

        result = generate_zero_defaults(Model)
        assert result == {"active": False}

    def test_optional_field(self):
        """Test generation of default for optional field.

        Note: When there's a union with None, the function prefers the non-None type.
        """

        class Model(BaseModel):
            name: str | None

        result = generate_zero_defaults(Model)
        # Prefers non-None type in union
        assert result == {"name": ""}

    def test_optional_with_non_none_choice(self):
        """Test optional union with non-None type returns non-None default."""

        class Model(BaseModel):
            value: str | int | None

        result = generate_zero_defaults(Model)
        # Should prefer non-None type
        assert result == {"value": ""}

    def test_list_field(self):
        """Test generation of default for list field."""

        class Model(BaseModel):
            items: list[str]

        result = generate_zero_defaults(Model)
        assert result == {"items": []}

    def test_list_with_min_length(self):
        """Test list with min_length constraint."""

        class Model(BaseModel):
            items: Annotated[list[str], Field(min_length=2)]

        result = generate_zero_defaults(Model)
        assert result == {"items": ["", ""]}

    def test_dict_field(self):
        """Test generation of default for dict field."""

        class Model(BaseModel):
            metadata: dict[str, str]

        result = generate_zero_defaults(Model)
        assert result == {"metadata": {}}

    def test_int_with_gt_constraint(self):
        """Test int with gt (greater than) constraint."""

        class Model(BaseModel):
            age: Annotated[int, Field(gt=0)]

        result = generate_zero_defaults(Model)
        assert result == {"age": 1}

    def test_int_with_ge_constraint(self):
        """Test int with ge (greater than or equal) constraint."""

        class Model(BaseModel):
            count: Annotated[int, Field(ge=5)]

        result = generate_zero_defaults(Model)
        assert result == {"count": 5}

    def test_float_with_gt_constraint(self):
        """Test float with gt constraint."""

        class Model(BaseModel):
            price: Annotated[float, Field(gt=10.0)]

        result = generate_zero_defaults(Model)
        assert result["price"] > 10.0
        assert isinstance(result["price"], float)

    def test_float_with_ge_constraint(self):
        """Test float with ge constraint."""

        class Model(BaseModel):
            price: Annotated[float, Field(ge=10.0)]

        result = generate_zero_defaults(Model)
        assert result == {"price": 10.0}

    def test_int_with_lt_constraint(self):
        """Test int with lt (less than) constraint."""

        class Model(BaseModel):
            score: Annotated[int, Field(lt=0)]

        result = generate_zero_defaults(Model)
        assert result == {"score": -1}

    def test_int_with_le_constraint(self):
        """Test int with le (less than or equal) constraint."""

        class Model(BaseModel):
            balance: Annotated[int, Field(le=-5)]

        result = generate_zero_defaults(Model)
        assert result == {"balance": -5}

    def test_float_with_lt_constraint(self):
        """Test float with lt constraint."""

        class Model(BaseModel):
            amount: Annotated[float, Field(lt=0.0)]

        result = generate_zero_defaults(Model)
        assert result["amount"] < 0.0

    def test_float_with_both_bounds(self):
        """Test float with both lower and upper bounds."""

        class Model(BaseModel):
            ratio: Annotated[float, Field(gt=1.0, lt=2.0)]

        result = generate_zero_defaults(Model)
        assert 1.0 < result["ratio"] < 2.0

    def test_string_with_min_length(self):
        """Test string with min_length constraint."""

        class Model(BaseModel):
            code: Annotated[str, Field(min_length=5)]

        result = generate_zero_defaults(Model)
        assert result == {"code": "xxxxx"}

    def test_literal_field(self):
        """Test Literal type field."""

        class Model(BaseModel):
            status: Literal["active", "inactive"]

        result = generate_zero_defaults(Model)
        assert result["status"] in ("active", "inactive")

    def test_literal_with_none(self):
        """Test Literal with None value."""

        class Model(BaseModel):
            value: Literal[None, "something"]

        result = generate_zero_defaults(Model)
        assert result == {"value": "something"}

    def test_enum_field(self):
        """Test Enum field."""

        class Model(BaseModel):
            color: Color

        result = generate_zero_defaults(Model)
        assert result["color"] in ("red", "green", "blue")

    def test_uuid_field(self):
        """Test UUID field."""

        class Model(BaseModel):
            id: uuid.UUID

        result = generate_zero_defaults(Model)
        assert result == {"id": str(uuid.UUID(int=0))}

    def test_datetime_field(self):
        """Test datetime field."""

        class Model(BaseModel):
            created_at: dt.datetime

        result = generate_zero_defaults(Model)
        assert result == {
            "created_at": dt.datetime(1970, 1, 1, tzinfo=dt.UTC).isoformat()
        }

    def test_date_field(self):
        """Test date field."""

        class Model(BaseModel):
            birth_date: dt.date

        result = generate_zero_defaults(Model)
        assert result == {"birth_date": dt.date(1970, 1, 1).isoformat()}

    def test_time_field(self):
        """Test time field."""

        class Model(BaseModel):
            meeting_time: dt.time

        result = generate_zero_defaults(Model)
        assert result == {"meeting_time": dt.time(0, 0, 0).isoformat()}

    def test_timedelta_field(self):
        """Test timedelta field."""

        class Model(BaseModel):
            duration: dt.timedelta

        result = generate_zero_defaults(Model)
        assert result == {"duration": 0}

    def test_nested_model(self):
        """Test nested Pydantic model."""

        class Model(BaseModel):
            user: NestedModel

        result = generate_zero_defaults(Model)
        assert result == {"user": {"name": "", "value": 0}}

    def test_circular_reference(self):
        """Test model with circular reference."""

        class Model(BaseModel):
            root: CircularModel

        result = generate_zero_defaults(Model)
        # CircularModel has optional child, so it should not be included
        assert result == {"root": {"name": ""}}

    def test_tuple_field(self):
        """Test tuple field."""

        class Model(BaseModel):
            coordinates: tuple[float, float]

        result = generate_zero_defaults(Model)
        assert result == {"coordinates": (0.0, 0.0)}

    def test_tuple_with_ellipsis(self):
        """Test tuple with ellipsis (variable length)."""

        class Model(BaseModel):
            items: tuple[str, ...]

        result = generate_zero_defaults(Model)
        assert result == {"items": ()}

    def test_tuple_with_ellipsis_and_min_length(self):
        """Test tuple with ellipsis and min_length constraint."""

        class Model(BaseModel):
            items: Annotated[tuple[int, ...], Field(min_length=3)]

        result = generate_zero_defaults(Model)
        assert result == {"items": (0, 0, 0)}

    def test_set_field(self):
        """Test set field."""

        class Model(BaseModel):
            tags: set[str]

        result = generate_zero_defaults(Model)
        assert result == {"tags": []}

    def test_set_with_min_length(self):
        """Test set with min_length constraint."""

        class Model(BaseModel):
            tags: Annotated[set[str], Field(min_length=2)]

        result = generate_zero_defaults(Model)
        assert set(result) == {"tags"}
        assert len(result["tags"]) == 2
        assert result["tags"][0] == ""
        assert len(set(result["tags"])) == 2

    def test_fields_with_defaults_excluded(self):
        """Test that fields with defaults are not included."""

        class Model(BaseModel):
            name: str
            age: int = 25
            active: bool = True

        result = generate_zero_defaults(Model)
        # Only name should be included, age and active have defaults
        assert result == {"name": ""}

    def test_fields_with_default_factory_excluded(self):
        """Test that fields with default_factory are not included."""

        class Model(BaseModel):
            name: str
            tags: list[str] = Field(default_factory=list)

        result = generate_zero_defaults(Model)
        assert result == {"name": ""}

    def test_complex_nested_model(self):
        """Test complex nested structure."""

        class Address(BaseModel):
            street: str
            city: str
            zipcode: Annotated[str, Field(min_length=5)]

        class Person(BaseModel):
            name: str
            age: Annotated[int, Field(gt=0)]
            addresses: list[Address]

        class Organization(BaseModel):
            name: str
            employees: list[Person]

        result = generate_zero_defaults(Organization)
        assert result == {
            "name": "",
            "employees": [],
        }

    def test_union_non_none_types(self):
        """Test union of non-None types."""

        class Model(BaseModel):
            value: str | int

        result = generate_zero_defaults(Model)
        # Should pick the first non-None type
        assert result == {"value": ""}

    def test_annotated_field(self):
        """Test Annotated field."""

        class Model(BaseModel):
            name: Annotated[str, "some metadata"]

        result = generate_zero_defaults(Model)
        assert result == {"name": ""}

    def test_bytes_field(self):
        """Test bytes field."""

        class Model(BaseModel):
            data: bytes

        result = generate_zero_defaults(Model)
        assert result == {"data": ""}

    def test_multiple_fields(self):
        """Test model with multiple fields of different types."""

        class Model(BaseModel):
            name: str
            age: Annotated[int, Field(ge=18)]
            email: str | None
            tags: list[str]
            metadata: dict[str, int]
            active: bool = True

        result = generate_zero_defaults(Model)
        # email is optional but prefers non-None type
        assert result == {
            "name": "",
            "age": 18,
            "email": "",
            "tags": [],
            "metadata": {},
        }

    def test_nested_optional_model(self):
        """Test nested model with optional fields."""

        class Inner(BaseModel):
            value: str
            optional_field: str | None = None

        class Outer(BaseModel):
            name: str
            inner: Inner

        result = generate_zero_defaults(Outer)
        # optional_field has a default, should not be included
        assert result == {"name": "", "inner": {"value": ""}}

    def test_deeply_nested_circular_model(self):
        """Test deeply nested model with circular references."""

        class Node(BaseModel):
            value: str
            children: list["Node"] = Field(default_factory=list)

        result = generate_zero_defaults(Node)
        # children has default_factory, should not be included
        assert result == {"value": ""}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
