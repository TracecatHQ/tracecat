import json
from datetime import datetime

import lark
import pytest
from pydantic import ValidationError

from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.logger import logger


def test_validate_schema():
    schema = {
        "start_time": {
            "type": "datetime",
            "description": "The start time",
        },
        "end_time": {
            "type": "datetime",
            "description": "The end time",
        },
        "nullable": {
            "type": "int | None",
            "description": "An nullable integer",
        },
        "optional_with_default": {
            "type": "int",
            "default": 1,
            "description": "An optional integer with a default value",
        },
        "list_of_strings": {
            "type": "list[str]",
            "description": "A list of strings",
        },
        "dict_of_int_to_float": {
            "type": "dict[int, float]",
            "description": "A dictionary mapping integers to floats",
        },
        "union_type": {
            "type": "int | str",
            "description": "Either an integer or a string",
        },
    }
    # Parse this a a list[ExpectedField]
    mapped = {k: ExpectedField(**v) for k, v in schema.items()}

    DynamicModel = create_expectation_model(mapped)

    # Test the model
    valid_data = {
        "start_time": "2023-05-17T10:00:00",
        "end_time": "2023-05-17T11:00:00",
        "nullable": 42,
        "list_of_strings": ["a", "b", "c"],
        "dict_of_int_to_float": {1: 1.0, 2: 2.0},
        "union_type": "test",
    }
    model_instance = DynamicModel(**valid_data)

    logger.info(model_instance.model_dump_json(indent=2))
    logger.info(json.dumps(model_instance.model_json_schema(), indent=2))

    assert isinstance(model_instance.start_time, datetime)
    assert isinstance(model_instance.end_time, datetime)
    assert model_instance.nullable == 42
    assert model_instance.optional_with_default == 1
    assert model_instance.list_of_strings == ["a", "b", "c"]
    assert model_instance.dict_of_int_to_float == {1: 1.0, 2: 2.0}
    assert model_instance.union_type == "test"


# Test with optional field omitted
def test_dynamic_model_with_optional_field_omitted():
    schema = {
        "start_time": {
            "type": "datetime",
            "description": "The start time",
        },
        "end_time": {
            "type": "datetime",
            "description": "The end time",
        },
        "nullable": {
            "type": "int | None",
            "description": "An nullable integer",
        },
        "optional_with_default": {
            "type": "int",
            "default": 1,  # Defining a default value makes the field optional
            "description": "An optional integer with a default value",
        },
        "list_of_strings": {
            "type": "list[str]",
            "description": "A list of strings",
        },
        "dict_of_int_to_float": {
            "type": "dict[int, float]",
            "description": "A dictionary mapping integers to floats",
        },
        "union_type": {
            "type": "int | str",
            "description": "Either an integer or a string",
        },
    }

    DynamicModel = create_expectation_model(schema)

    valid_data_no_optional = {
        "start_time": "2023-05-17T10:00:00",
        "end_time": "2023-05-17T11:00:00",
        "list_of_strings": [],
        "dict_of_int_to_float": {},
        "nullable": None,
        "union_type": 123,
    }
    model_instance_no_optional = DynamicModel(**valid_data_no_optional)
    assert model_instance_no_optional.nullable is None
    assert model_instance_no_optional.optional_with_default == 1


# Test with invalid data
def test_dynamic_model_with_invalid_data():
    schema = {
        "start_time": {
            "type": "datetime",
            "description": "The start time",
        },
        "end_time": {
            "type": "datetime",
            "description": "The end time",
        },
        "nullable": {
            "type": "int | None",
            "description": "A nullable integer",
        },
        "optional_with_default": {
            "type": "int",
            "default": 1,
            "description": "An optional integer with a default value",
        },
        "list_of_strings": {
            "type": "list[str]",
            "description": "A list of strings",
        },
        "dict_of_int_to_float": {
            "type": "dict[int, float]",
            "description": "A dictionary mapping integers to floats",
        },
        "union_type": {
            "type": "int | str",
            "description": "Either an integer or a string",
        },
    }

    DynamicModel = create_expectation_model(schema)
    with pytest.raises(ValidationError):
        DynamicModel(
            start_time="invalid_datetime",
            end_time="2023-05-17T11:00:00",
            nullable="not an int",  # Should be int or None
            list_of_strings=[1, 2, 3],  # Should be strings
            dict_of_int_to_float={"a": "b"},  # Invalid types
            union_type={},  # Neither int nor str
        )


def test_validate_schema_success():
    schema = {
        "start_time": {
            "type": "datetime",
            "description": "The start time",
        },
        "end_time": {
            "type": "datetime",
            "description": "The end time",
        },
        "duration": {
            "type": "duration",
            "description": "The duration",
        },
        "integer": {
            "type": "int",
            "description": "An integer",
        },
        "string": {
            "type": "str",
            "description": "A string",
        },
        "boolean": {
            "type": "bool",
            "description": "A boolean",
        },
        "float": {
            "type": "float",
            "description": "A float",
        },
        "any": {
            "type": "any",
            "description": "Any type",
        },
        "list_any": {
            "type": "list[any]",
            "description": "A list of any type",
        },
        "list_typed": {
            "type": "list[str]",
            "description": "A list of strings",
        },
    }

    mapped = {k: ExpectedField(**v) for k, v in schema.items()}
    DynamicModel = create_expectation_model(mapped)

    test_input = {
        "start_time": "2023-01-01T00:00:00",
        "end_time": "2023-01-01T00:00:00Z",
        "duration": "P1D",
        "integer": 1,
        "string": "hello",
        "boolean": True,
        "float": 1.0,
        "any": "hello",
        "list_any": [1, "hello", True],
        "list_typed": ["test", "test"],
    }

    validated_data = DynamicModel(**test_input)
    assert validated_data.model_dump(mode="json") == test_input


def test_validate_schema_failure():
    schema = {
        "start_time": {"type": "datetime", "description": "The start time"},
        "end_time": {"type": "datetime", "description": "The end time"},
    }

    mapped = {k: ExpectedField(**v) for k, v in schema.items()}
    DynamicModel = create_expectation_model(mapped)

    test_input = {
        "start_time": "2023-01-01T00:00:00",
        "end_time": "invalid time!",
    }

    with pytest.raises(ValidationError) as e:
        DynamicModel(**test_input)

    assert "end_time" in str(e.value)


@pytest.mark.parametrize(
    "status,priority",
    [
        ("PENDING", "low"),
        ("running", "low"),
        ("Completed", "low"),
    ],
)
def test_validate_schema_with_enum(status, priority):
    schema = {
        "status": {
            "type": 'enum["PENDING", "running", "Completed"]',
            "description": "The status of the job",
        },
        "priority": {
            "type": 'enum["low", "medium", "high"]',
            "description": "The priority level",
            "default": "low",
        },
    }

    mapped = {k: ExpectedField(**v) for k, v in schema.items()}
    model = create_expectation_model(mapped)

    # Test with provided priority
    model_instance = model(status=status, priority=priority)
    assert model_instance.status.__class__.__name__ == "EnumStatus"
    assert model_instance.priority.__class__.__name__ == "EnumPriority"

    # Test default priority
    model_instance_default = model(status=status)
    assert str(model_instance_default.priority) == "low"


@pytest.mark.parametrize(
    "schema_def,error_type,error_message",
    [
        (
            {"status": {"type": "enum[]", "description": "Empty enum"}},
            lark.exceptions.UnexpectedCharacters,
            "No terminal matches ']'",
        ),
        (
            {
                "status": {
                    "type": 'enum["Pending", "PENDING"]',
                    "description": "Duplicate values",
                }
            },
            lark.exceptions.VisitError,
            "Duplicate enum value",
        ),
    ],
)
def test_validate_schema_with_invalid_enum_definition(
    schema_def, error_type, error_message
):
    with pytest.raises(error_type, match=error_message):
        mapped = {k: ExpectedField(**v) for k, v in schema_def.items()}
        create_expectation_model(mapped)


@pytest.mark.parametrize(
    "invalid_value",
    [
        "invalid_status",
        "INVALID",
        "pending!",
        "",
    ],
)
def test_validate_schema_with_invalid_enum_values(invalid_value):
    schema = {
        "status": {
            "type": 'enum["PENDING", "running", "Completed"]',
            "description": "The status of the job",
        }
    }

    mapped = {k: ExpectedField(**v) for k, v in schema.items()}
    DynamicModel = create_expectation_model(mapped)

    with pytest.raises(ValidationError):
        DynamicModel(status=invalid_value)
