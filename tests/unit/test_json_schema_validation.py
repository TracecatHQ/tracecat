from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from tracecat.validation.common import json_schema_to_pydantic

_ADDRESS_DEFS = {
    "Address": {
        "type": "object",
        "properties": {
            "street": {"type": "string"},
            "city": {"type": "string"},
        },
        "required": ["city"],
    }
}

_COMPLEX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The user's name"},
        "age": {"type": "integer"},
        "is_student": {"type": "boolean"},
        "grades": {"type": "array", "items": {"type": "number"}},
        "user": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        },
        "items_list": {
            "type": "array",
            "items": {
                "type": "object",
                "title": "ListItem",
                "properties": {
                    "item_id": {"type": "integer"},
                    "item_name": {"type": "string"},
                },
                "required": ["item_id", "item_name"],
            },
        },
        "address": {"$ref": "#/definitions/Address"},
    },
    "required": ["name"],
    "definitions": _ADDRESS_DEFS,
}


def test_complex_schema_validates_conforming_data() -> None:
    Model = json_schema_to_pydantic(_COMPLEX_SCHEMA)

    validated = Model.model_validate(
        {
            "name": "Ada",
            "age": 36,
            "is_student": False,
            "grades": [1.5, 2],
            "user": {"name": "nested"},
            "items_list": [{"item_id": 1, "item_name": "one"}],
            "address": {"city": "London"},
        },
    )
    dumped = validated.model_dump()

    assert dumped["name"] == "Ada"
    assert dumped["grades"] == [1.5, 2.0]
    assert dumped["user"] == {"name": "nested", "age": None}
    assert dumped["items_list"] == [{"item_id": 1, "item_name": "one"}]
    assert dumped["address"] == {"street": None, "city": "London"}
    assert Model.model_fields["name"].description == "The user's name"

    minimal = Model.model_validate({"name": "Ada"}).model_dump()
    assert {k: v for k, v in minimal.items() if k != "name"} == {
        "age": None,
        "is_student": None,
        "grades": None,
        "user": None,
        "items_list": None,
        "address": None,
    }


@pytest.mark.parametrize(
    ("data", "error_loc"),
    [
        pytest.param({}, ("name",), id="missing-required"),
        pytest.param({"name": "Ada", "age": "abc"}, ("age",), id="wrong-scalar-type"),
        pytest.param({"name": "Ada", "grades": ["x"]}, ("grades", 0), id="array-item"),
        pytest.param(
            {"name": "Ada", "user": {"name": 5}}, ("user", "name"), id="nested-object"
        ),
        pytest.param(
            {"name": "Ada", "items_list": [{"item_id": 1}]},
            ("items_list", 0, "item_name"),
            id="array-of-objects-required",
        ),
        pytest.param(
            {"name": "Ada", "address": {"street": "Main"}},
            ("address", "city"),
            id="ref-required",
        ),
    ],
)
def test_complex_schema_rejects_nonconforming_data(
    data: dict[str, Any], error_loc: tuple[str | int, ...]
) -> None:
    Model = json_schema_to_pydantic(_COMPLEX_SCHEMA)

    with pytest.raises(ValidationError) as exc_info:
        Model.model_validate(data)

    assert [error["loc"] for error in exc_info.value.errors()] == [error_loc]


def test_template_expressions_are_accepted_for_typed_fields() -> None:
    Model = json_schema_to_pydantic(_COMPLEX_SCHEMA)

    validated = Model.model_validate({"name": "Ada", "age": "${{ ACTIONS.a.result }}"})

    assert validated.model_dump()["age"] == "${{ ACTIONS.a.result }}"


def test_enum_fields() -> None:
    schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "closed"],
                "title": "ticket_status",
            },
            "severity": {"type": "integer", "enum": [1, 2, 3, 4]},
        },
        "required": ["status"],
    }
    Model = json_schema_to_pydantic(schema)

    assert Model.model_validate({"status": "open", "severity": 2}).model_dump() == {
        "status": "open",
        "severity": 2,
    }
    with pytest.raises(ValidationError):
        Model.model_validate({"status": "bogus"})
    with pytest.raises(ValidationError):
        Model.model_validate({"status": "open", "severity": 5})


def test_ref_to_enum_in_defs() -> None:
    """Ensure $ref to an enum schema in $defs restricts values to the enum.

    Reproduces schema shape saved for template actions where properties reference
    an enum via $ref to #/$defs/EnumModel.
    """
    schema = {
        "type": "object",
        "title": "llm__anthropic__call",
        "properties": {
            "model": {
                "$ref": "#/$defs/EnumModel",
                "description": "Model to use.",
            }
        },
        "required": ["model"],
        "$defs": {
            "EnumModel": {
                "type": "string",
                "enum": [
                    "claude-3-5-sonnet-20240620",
                    "claude-3-5-haiku-20240307",
                    "claude-3-5-opus-20240229",
                ],
                "title": "EnumModel",
            }
        },
    }
    Model = json_schema_to_pydantic(schema)

    validated = Model.model_validate({"model": "claude-3-5-haiku-20240307"})
    assert validated.model_dump() == {"model": "claude-3-5-haiku-20240307"}
    with pytest.raises(ValidationError):
        Model.model_validate({"model": "gpt-4o"})


def test_invalid_schema_type_accepts_any_value() -> None:
    schema = {"type": "object", "properties": {"field_a": {"type": "invalid_type"}}}
    Model = json_schema_to_pydantic(schema)

    for value in ({"nested": 1}, [1, "a"], "text", None):
        assert Model.model_validate({"field_a": value}).model_dump() == {
            "field_a": value
        }


def test_datetime_format() -> None:
    """String fields with 'date-time' format validate to datetime."""
    schema = {
        "type": "object",
        "title": "DateTimeTest",
        "properties": {
            "start": {
                "type": "string",
                "format": "date-time",
                "description": "The start date.",
            },
            "end": {
                "type": "string",
                "format": "date-time",
                "description": "The end date.",
            },
            "regular_string": {
                "type": "string",
                "description": "A regular string field.",
            },
        },
        "required": ["start", "end"],
    }
    Model = json_schema_to_pydantic(schema)

    validated = Model.model_validate(
        {
            "start": datetime(2025, 6, 26, 15, 51, 8, 346252),
            "end": "2025-06-26T16:51:08.346252",
            "regular_string": "2025-06-26T16:51:08.346252",
        },
    ).model_dump()

    assert validated["start"] == datetime(2025, 6, 26, 15, 51, 8, 346252)
    assert validated["end"] == datetime(2025, 6, 26, 16, 51, 8, 346252)
    assert validated["regular_string"] == "2025-06-26T16:51:08.346252"
    with pytest.raises(ValidationError):
        Model.model_validate({"start": "not-a-date", "end": "2025-06-26T16:51:08"})
