import json
from datetime import datetime
from typing import Any

import lark
import pytest
from pydantic import ValidationError

from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.logger import logger
from tracecat.workflow.management.utils import build_trigger_inputs_schema


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
    model_instance: Any = DynamicModel(**valid_data)

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
    model_instance_no_optional: Any = DynamicModel(**valid_data_no_optional)
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


def test_dynamic_model_with_bare_dict_type():
    schema = {
        "payload": {
            "type": "dict | None",
            "description": "Optional untyped dictionary payload.",
        },
    }

    DynamicModel = create_expectation_model(schema)

    inst_ok: Any = DynamicModel(payload={"foo": "bar"})
    assert inst_ok.payload == {"foo": "bar"}
    inst_none: Any = DynamicModel(payload=None)
    assert inst_none.payload is None

    with pytest.raises(ValidationError):
        DynamicModel(payload="not-a-dict")


def test_validate_schema_success():
    schema: dict[str, Any] = {
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
    schema: dict[str, Any] = {
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
    schema: dict[str, Any] = {
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
    model_instance: Any = model(status=status, priority=priority)
    assert isinstance(model_instance.status, str)
    assert model_instance.status == status
    assert model_instance.priority == priority

    # Test default priority
    model_instance_default: Any = model(status=status)
    assert model_instance_default.priority == "low"


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
    schema: dict[str, Any] = {
        "status": {
            "type": 'enum["PENDING", "running", "Completed"]',
            "description": "The status of the job",
        }
    }

    mapped = {k: ExpectedField(**v) for k, v in schema.items()}
    DynamicModel = create_expectation_model(mapped)

    with pytest.raises(ValidationError):
        DynamicModel(status=invalid_value)


def test_trigger_input_schema_default_values_are_applied():
    """Test that default values are correctly applied when validating trigger inputs."""
    # Define a schema with multiple fields that have default values
    expects = {
        "case_id": {"type": "str", "description": "Case identifier"},
        "severity": {"type": "enum['low','high']"},
        "count": {"type": "int", "default": 1},
        "enabled": {"type": "bool", "default": True},
        "message": {"type": "str", "default": "Hello, World!"},
        "threshold": {"type": "float", "default": 0.5},
        "tags": {"type": "list[str]", "default": ["default", "tag"]},
    }

    # Build the JSON schema
    schema = build_trigger_inputs_schema(expects)
    assert schema is not None

    # Verify that fields with defaults are not in the required list
    assert "count" not in schema.get("required", [])
    assert "enabled" not in schema.get("required", [])
    assert "message" not in schema.get("required", [])
    assert "threshold" not in schema.get("required", [])
    assert "tags" not in schema.get("required", [])

    # Verify that required fields without defaults are in the required list
    assert "case_id" in schema.get("required", [])
    assert "severity" in schema.get("required", [])

    # Verify default values are present in the schema properties
    properties = schema.get("properties", {})
    assert properties["count"]["default"] == 1
    assert properties["enabled"]["default"] is True
    assert properties["message"]["default"] == "Hello, World!"
    assert properties["threshold"]["default"] == 0.5
    assert properties["tags"]["default"] == ["default", "tag"]

    # Create an expectation model from the schema
    validated_fields = {
        field_name: ExpectedField.model_validate(field_schema)
        for field_name, field_schema in expects.items()
    }
    TriggerInputModel = create_expectation_model(
        validated_fields, model_name="TriggerInputModel"
    )

    # Test 1: Validate trigger inputs with all required fields but omitting fields with defaults
    trigger_inputs_minimal = {
        "case_id": "case-123",
        "severity": "low",
    }

    # Create model instance - defaults should be applied
    validated_instance: Any = TriggerInputModel(**trigger_inputs_minimal)

    # Verify that default values were applied
    assert validated_instance.case_id == "case-123"
    assert validated_instance.severity == "low"
    assert validated_instance.count == 1  # Default applied
    assert validated_instance.enabled is True  # Default applied
    assert validated_instance.message == "Hello, World!"  # Default applied
    assert validated_instance.threshold == 0.5  # Default applied
    assert validated_instance.tags == ["default", "tag"]  # Default applied

    # Test 2: Validate trigger inputs where some defaults are overridden
    trigger_inputs_partial = {
        "case_id": "case-456",
        "severity": "high",
        "count": 42,  # Override default
        "enabled": False,  # Override default
        # message, threshold, and tags should use defaults
    }

    validated_instance_partial: Any = TriggerInputModel(**trigger_inputs_partial)

    # Verify overridden values
    assert validated_instance_partial.case_id == "case-456"
    assert validated_instance_partial.severity == "high"
    assert validated_instance_partial.count == 42  # Overridden
    assert validated_instance_partial.enabled is False  # Overridden

    # Verify defaults still applied for omitted fields
    assert validated_instance_partial.message == "Hello, World!"  # Default applied
    assert validated_instance_partial.threshold == 0.5  # Default applied
    assert validated_instance_partial.tags == ["default", "tag"]  # Default applied

    # Test 3: Validate trigger inputs with all fields provided (no defaults used)
    trigger_inputs_complete = {
        "case_id": "case-789",
        "severity": "high",
        "count": 100,
        "enabled": True,
        "message": "Custom message",
        "threshold": 0.75,
        "tags": ["custom", "tags"],
    }

    validated_instance_complete: Any = TriggerInputModel(**trigger_inputs_complete)

    # Verify all values are as provided (no defaults used)
    assert validated_instance_complete.case_id == "case-789"
    assert validated_instance_complete.severity == "high"
    assert validated_instance_complete.count == 100
    assert validated_instance_complete.enabled is True
    assert validated_instance_complete.message == "Custom message"
    assert validated_instance_complete.threshold == 0.75
    assert validated_instance_complete.tags == ["custom", "tags"]
