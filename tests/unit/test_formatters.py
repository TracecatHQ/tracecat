"""Tests for the formatters module."""

import csv
import io
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

from tracecat.expressions.formatters import (
    _format_csv,
    _format_html,
    _format_markdown,
    _format_xml,
    tabulate,
)


@pytest.fixture
def simple_data():
    """Basic test data with normalized keys."""
    return [
        {"name": "Alice", "age": 30, "city": "New York"},
        {"name": "Bob", "age": 25, "city": "Los Angeles"},
    ]


@pytest.fixture
def missing_data():
    """Test data with missing values."""
    return [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "city": "Los Angeles"},
        {"age": 35, "city": "Chicago"},
    ]


def test_markdown_simple_format(simple_data):
    lines = _format_markdown(simple_data).strip().split("\n")

    # Header, separator, and one row per item.
    assert len(lines) == 4
    for line in lines:
        assert line.startswith("|") and line.endswith("|")
    for key in ["name", "age", "city"]:
        assert key in lines[0]
    data_text = "\n".join(lines[2:])
    for value in ["Alice", "30", "New York", "Bob", "25", "Los Angeles"]:
        assert value in data_text


@pytest.mark.parametrize(
    "input_data",
    [
        pytest.param([], id="empty_list"),
        pytest.param([{}], id="empty_dict"),
        pytest.param([{"a": None}], id="none_value"),
    ],
)
def test_markdown_edge_cases(input_data):
    result = _format_markdown(input_data)

    if all(not item for item in input_data):
        assert result == ""
    else:
        lines = result.strip().split("\n")
        assert len(lines) >= 3
        for line in lines:
            assert line.startswith("|") and line.endswith("|")
        assert "-" in lines[1]


@pytest.mark.parametrize(
    ("formatter", "empty_cell"),
    [(_format_markdown, "||"), (_format_html, "<td></td>")],
    ids=["markdown", "html"],
)
def test_missing_values_render_empty_cells(
    missing_data, formatter: Callable[[list[dict[str, Any]]], str], empty_cell: str
):
    assert empty_cell in formatter(missing_data)


def test_html_simple_format(simple_data):
    result = _format_html(simple_data)

    for tag in ["<table>", "</table>", "<thead>", "<tbody>"]:
        assert tag in result
    for key in ["name", "age", "city"]:
        assert f"<th>{key}</th>" in result
    assert "<td>Alice</td>" in result
    assert "<td>New York</td>" in result


def test_html_escapes_special_characters():
    result = _format_html([{"content": "<script>alert('xss')</script>"}])

    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "&apos;" in result or "&#x27;" in result or "&#39;" in result


def test_csv_simple_format(simple_data):
    rows = list(csv.DictReader(io.StringIO(_format_csv(simple_data))))

    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"
    assert rows[0]["age"] == "30"
    assert rows[1]["city"] == "Los Angeles"


def test_csv_missing_values(missing_data):
    rows = list(csv.DictReader(io.StringIO(_format_csv(missing_data))))

    assert rows[0].get("city", "NOT_EMPTY") == ""
    assert rows[1].get("age", "NOT_EMPTY") == ""


def test_csv_complex_types():
    complex_data = [
        {
            "name": "Alice",
            "details": {"age": 30, "city": "New York"},
            "dates": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
            "active": True,
        },
        {
            "name": "Bob",
            "details": {"age": 25, "city": "Los Angeles"},
            "dates": None,
            "active": False,
        },
    ]
    rows = list(csv.DictReader(io.StringIO(_format_csv(complex_data))))

    assert all(isinstance(value, str) for row in rows for value in row.values())


def test_xml_simple_format(simple_data):
    root = ET.fromstring(_format_xml(simple_data))

    assert root.tag == "items"
    items = root.findall("item")
    assert len(items) == 2
    name_elem = items[0].find("name")
    age_elem = items[0].find("age")
    assert name_elem is not None and name_elem.text == "Alice"
    assert age_elem is not None and age_elem.text == "30"


def test_xml_escapes_special_characters():
    root = ET.fromstring(_format_xml([{"content": "<tag>value</tag>"}]))

    content_elem = root.find("item/content")
    assert content_elem is not None
    assert content_elem.text == "<tag>value</tag>"


@pytest.mark.parametrize(
    ("format_name", "formatter"),
    [
        ("markdown", _format_markdown),
        ("html", _format_html),
        ("csv", _format_csv),
        ("xml", _format_xml),
    ],
)
def test_tabulate_dispatch(
    simple_data,
    format_name: str,
    formatter: Callable[[list[dict[str, Any]]], str],
):
    assert tabulate(simple_data, format_name) == formatter(simple_data)  # type: ignore[arg-type]
