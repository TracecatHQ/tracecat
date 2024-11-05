from typing import Optional

import pytest
from pydantic import BaseModel

from tracecat.validation.common import json_schema_to_pydantic


def test_simple_schema():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    assert Model.__annotations__["name"] is str
    assert Model.__annotations__["age"] is Optional[int]  # noqa: UP007


def test_nested_object():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    assert issubclass(Model.__annotations__["user"], BaseModel)
    assert Model.__annotations__["user"].__annotations__["name"] is str
    assert Model.__annotations__["user"].__annotations__["age"] is Optional[int]  # noqa: UP007


def test_array_field():
    schema = {
        "type": "object",
        "properties": {"numbers": {"type": "array", "items": {"type": "integer"}}},
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    assert Model.__annotations__["numbers"] is Optional[list[int]]  # noqa: UP007


def test_reference():
    schema = {
        "type": "object",
        "properties": {"address": {"$ref": "#/definitions/Address"}},
        "definitions": {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    assert issubclass(Model.__annotations__["address"], BaseModel)
    assert Model.__annotations__["address"].__annotations__["street"] is str
    assert Model.__annotations__["address"].__annotations__["city"] is str


def test_required_fields():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    Model = json_schema_to_pydantic(schema)

    assert Model.__annotations__["name"] is str
    assert Model.__annotations__["age"] is Optional[int]  # noqa: UP007


def test_field_description():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The user's name"}},
    }

    Model = json_schema_to_pydantic(schema)

    assert Model.model_fields["name"].description == "The user's name"


def test_complex_schema():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "is_student": {"type": "boolean"},
            "grades": {"type": "array", "items": {"type": "number"}},
            "address": {"$ref": "#/definitions/Address"},
        },
        "required": ["name", "age"],
        "definitions": {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    assert isinstance(Model.__annotations__["name"], str)
    assert Model.__annotations__["age"] is int
    assert Model.__annotations__["is_student"] is Optional[bool]  # noqa: UP007
    assert Model.__annotations__["grades"] is Optional[list[float]]  # noqa: UP007
    assert issubclass(Model.__annotations__["address"], BaseModel)


def test_invalid_schema():
    schema = {"type": "invalid_type", "properties": {}}

    with pytest.raises(ValueError, match="Unsupported schema type: 'invalid_type'"):
        json_schema_to_pydantic(schema)


# Add more tests as needed
