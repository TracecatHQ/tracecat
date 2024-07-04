import pydantic
import pytest

from tracecat.validation import SchemaValidatorFactory


def test_validate_schema_success():
    schema = {
        "start_time": "datetime",
        "end_time": "datetime",
        "duration": "duration",
        "integer": "int",
        "string": "str",
        "boolean": "bool",
        "float": "float",
        "any": "any",
        "list_any": "list",
        "list_typed": "list[str]",
        "nested": {
            "a": "int",
            "b": "str",
            "c": "bool",
            "d": "float",
            "e": "str",
            "again1": "int",
            "again2": {
                "again2_a": "str",
                "again2_b": "int",
            },
        },
        "list": "list[$nested]",  # Check that it resolves the list reference
        "another": "$nested2",  # Check that it resolves the dict reference
        "$refs": {
            "nested": {
                "a": "int",
                "b": "str",
                "c": "bool",
                "d": "float",
            },
            "nested2": {
                "field1": "int",
                "field2": "str",
                "field3": "bool",
            },
        },
    }

    test_input = {
        "start_time": "2023-01-01T00:00:00",
        "end_time": "2023-01-01T00:00:00Z",
        "duration": "P1D",
        "integer": 1,
        "string": "hello",
        "boolean": True,
        "float": 1.0,
        "any": "hello",
        "list_any": [
            1,
            "hello",
            True,
        ],
        "list_typed": ["test", "test"],
        "nested": {
            "a": 1,
            "b": "hello",
            "c": True,
            "d": 1.0,
            "e": "2023-01-01",
            "again1": 1,
            "again2": {
                "again2_a": "hello",
                "again2_b": 1,
            },
        },
        "list": [
            {
                "a": 1,
                "b": "hello",
                "c": True,
                "d": 1.0,
            }
        ],
        "another": {
            "field1": 1,
            "field2": "hello",
            "field3": True,
        },
    }

    factory = SchemaValidatorFactory(schema)
    ExpectedInputsValidator = factory.create()
    validated_data = ExpectedInputsValidator(**test_input)
    assert validated_data.model_dump(mode="json") == test_input


def test_validate_schema_failure():
    schema = {
        "start_time": "datetime",
        "end_time": "datetime",
    }

    test_input = {
        "start_time": "2023-01-01T00:00:00",
        "end_time": "invalid time!",
    }

    factory = SchemaValidatorFactory(schema)
    ExpectedInputsValidator = factory.create()

    with pytest.raises(pydantic.ValidationError) as e:
        ExpectedInputsValidator(**test_input)

    assert "end_time" in str(e.value)
