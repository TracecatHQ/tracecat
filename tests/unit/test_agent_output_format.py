from __future__ import annotations

import pytest

from tracecat.agent.common.output_format import (
    build_output_schema,
    build_sdk_output_format,
)


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


def test_build_sdk_output_format_unwraps_schema_bundle() -> None:
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


@pytest.mark.parametrize(
    ("alias", "result_schema"),
    [
        ("str", {"type": "string"}),
        ("int", {"type": "integer"}),
        ("float", {"type": "number"}),
        ("bool", {"type": "boolean"}),
        ("list[str]", {"type": "array", "items": {"type": "string"}}),
        ("list[int]", {"type": "array", "items": {"type": "integer"}}),
        ("list[float]", {"type": "array", "items": {"type": "number"}}),
        ("list[bool]", {"type": "array", "items": {"type": "boolean"}}),
    ],
)
def test_shared_schema_preserves_primitive_envelope(alias, result_schema) -> None:
    schema = {
        "type": "object",
        "properties": {"result": result_schema},
        "required": ["result"],
        "additionalProperties": False,
    }
    assert build_output_schema(alias) == schema
    assert build_sdk_output_format(alias) == {"type": "json_schema", "schema": schema}


@pytest.mark.parametrize("format_name", ["raw", "claude", "openai", "gemini", "bundle"])
def test_shared_schema_normalizes_provider_formats(format_name) -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
    formats = {
        "raw": schema,
        "claude": {"type": "json_schema", "schema": schema},
        "openai": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema},
        },
        "gemini": {"response_schema": schema},
        "bundle": {"name": "answer", "schema": schema, "strict": True},
    }
    assert build_output_schema(formats[format_name]) == schema


@pytest.mark.parametrize("output_type", [None, "unknown"])
def test_shared_schema_has_no_format_for_absent_or_unknown_type(output_type) -> None:
    assert build_output_schema(output_type) is None
    assert build_sdk_output_format(output_type) is None
