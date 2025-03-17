"""Tests for the formatters module."""

import csv
import io
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest

from tracecat.expressions.formatters import (
    _format_csv,
    _format_html,
    _format_markdown,
    _format_xml,
    format_table,
)


# Common test data fixtures
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


@pytest.fixture
def complex_data():
    """Test data with nested structures and various types."""
    return [
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


# Markdown format tests
class TestMarkdown:
    """Test cases for markdown formatter."""

    def test_simple_format(self, simple_data):
        """Test markdown formatting with simple data."""
        result = _format_markdown(simple_data)
        lines = result.strip().split("\n")

        # Check structure
        assert len(lines) == 4  # Header + separator + 2 data rows
        assert all(line.startswith("| ") and line.endswith(" |") for line in lines)

        # Check content
        header = lines[0]
        assert all(key in header for key in ["name", "age", "city"])
        assert "Alice" in lines[2] and "30" in lines[2] and "New York" in lines[2]
        assert "Bob" in lines[3] and "25" in lines[3] and "Los Angeles" in lines[3]

    def test_missing_values(self, missing_data):
        """Test markdown formatting with missing values."""
        result = _format_markdown(missing_data)
        lines = result.strip().split("\n")

        # Verify empty cells are handled correctly
        assert len(lines) == 5  # Header + separator + 3 data rows
        assert "||" in lines[2]  # Empty cell representation

    @pytest.mark.parametrize(
        "input_data",
        [
            pytest.param([], id="empty_list"),
            pytest.param([{}], id="empty_dict"),
            pytest.param([{"a": None}], id="none_value"),
        ],
    )
    def test_edge_cases(self, input_data):
        """Test markdown formatting with edge cases."""
        result = _format_markdown(input_data)
        if not input_data:
            assert result == ""
        else:
            assert result.startswith("| ")
            assert result.endswith(" |")


# HTML format tests
class TestHTML:
    """Test cases for HTML formatter."""

    def test_simple_format(self, simple_data):
        """Test HTML formatting with simple data."""
        result = _format_html(simple_data)

        # Check structure
        assert result.startswith("<table>")
        assert result.endswith("</table>")
        assert "<thead>" in result and "</thead>" in result
        assert "<tbody>" in result and "</tbody>" in result

        # Check content
        assert all(f"<th>{key}</th>" in result for key in ["name", "age", "city"])
        assert "<td>Alice</td>" in result
        assert "<td>New York</td>" in result

    def test_special_characters(self):
        """Test HTML escaping of special characters."""
        data = [{"content": "<script>alert('xss')</script>"}]
        result = _format_html(data)

        assert "&lt;script&gt;" in result
        assert "&apos;" in result or "&#x27;" in result
        assert "<script>" not in result

    def test_missing_values(self, missing_data):
        """Test HTML formatting with missing values."""
        result = _format_html(missing_data)
        assert "<td></td>" in result


# CSV format tests
class TestCSV:
    """Test cases for CSV formatter."""

    def test_simple_format(self, simple_data):
        """Test CSV formatting with simple data."""
        result = _format_csv(simple_data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"
        assert rows[0]["age"] == "30"
        assert rows[1]["city"] == "Los Angeles"

    def test_missing_values(self, missing_data):
        """Test CSV formatting with missing values."""
        result = _format_csv(missing_data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        assert rows[0].get("city", "MISSING") == ""
        assert rows[1].get("age", "MISSING") == ""

    def test_complex_types(self, complex_data):
        """Test CSV formatting with complex data types."""
        result = _format_csv(complex_data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        # Verify all values are converted to strings
        assert all(isinstance(v, str) for row in rows for v in row.values())


# XML format tests
class TestXML:
    """Test cases for XML formatter."""

    def test_simple_format(self, simple_data):
        """Test XML formatting with simple data."""
        result = _format_xml(simple_data)
        root = ET.fromstring(result)

        assert root.tag == "items"
        items = root.findall("item")
        assert len(items) == 2

        first_item = items[0]
        name_elem = first_item.find("name")
        age_elem = first_item.find("age")

        assert name_elem is not None
        assert age_elem is not None
        assert name_elem.text == "Alice"
        assert age_elem.text == "30"

    def test_special_characters(self):
        """Test XML escaping of special characters."""
        data = [{"content": "<tag>value</tag>"}]
        result = _format_xml(data)
        root = ET.fromstring(result)

        content = root.find("item/content")
        assert content is not None
        assert content.text == "<tag>value</tag>"
        assert "&lt;" not in content.text  # XML handles escaping internally


# Integration tests
def test_format_table_dispatch(simple_data):
    """Test format_table dispatches to correct formatter."""
    formats = ["markdown", "html", "csv", "xml"]
    formatters = [_format_markdown, _format_html, _format_csv, _format_xml]

    for format_name, formatter in zip(formats, formatters, strict=False):
        result = format_table(simple_data, format_name)  # type: ignore
        direct_result = formatter(simple_data)
        assert result == direct_result


def test_format_table_invalid_format(simple_data):
    """Test format_table with invalid format."""
    with pytest.raises(ValueError, match="Unsupported format"):
        format_table(simple_data, "invalid")  # type: ignore
