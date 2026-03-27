from __future__ import annotations

from tracecat.agent.common.output_format import build_sdk_output_format


def test_build_sdk_output_format_accepts_raw_json_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }

    output_format = build_sdk_output_format(schema)

    assert output_format == {
        "type": "json_schema",
        "schema": schema,
    }


def test_build_sdk_output_format_unwraps_litellm_style_schema_bundle() -> None:
    output_type = {
        "name": "user_data",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "format": "email"}},
            "required": ["email"],
            "additionalProperties": False,
        },
    }

    output_format = build_sdk_output_format(output_type)

    assert output_format == {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "format": "email"}},
            "required": ["email"],
            "additionalProperties": False,
        },
    }


def test_build_sdk_output_format_unwraps_json_schema_wrapper() -> None:
    output_type = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    }

    output_format = build_sdk_output_format(output_type)

    assert output_format == {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    }


def test_build_sdk_output_format_wraps_primitive_output_type() -> None:
    output_format = build_sdk_output_format("str")

    assert output_format == {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
    }
