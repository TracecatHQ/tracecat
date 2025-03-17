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

        # Check structure - we expect header, separator, and data rows
        assert len(lines) == 4, (
            f"Expected 4 lines (header+separator+2 rows), but got {len(lines)}:\n{result}"
        )

        # Just check that each line has pipes at the beginning and end (don't enforce spaces)
        for i, line in enumerate(lines):
            assert line.startswith("|") and line.endswith("|"), (
                f"Line {i} should start and end with pipe characters:\n{line}"
            )

        # Check content - we need all keys and values in the table
        header = lines[0]
        for key in ["name", "age", "city"]:
            assert key in header, f"Header missing '{key}':\n{header}"

        # Check that data rows contain the expected values (don't care about exact position)
        data_text = "\n".join(lines[2:])  # All rows except header and separator
        expected_values = ["Alice", "30", "New York", "Bob", "25", "Los Angeles"]
        for value in expected_values:
            assert value in data_text, (
                f"Output missing '{value}' in data rows:\n{data_text}"
            )

    def test_missing_values(self, missing_data):
        """Test markdown formatting with missing values."""
        result = _format_markdown(missing_data)
        lines = result.strip().split("\n")

        # Verify we have the expected number of lines
        assert len(lines) == 5, f"Expected 5 lines, but got {len(lines)}:\n{result}"

        # Check for empty cells - in efficient format, this will be "||"
        # (two adjacent pipe characters)
        assert "||" in result, (
            f"Expected empty cell ('||') somewhere in result:\n{result}"
        )

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

        if not input_data or all(not item for item in input_data):
            # For empty list or list with only empty dictionaries, expect empty output
            assert result == "", (
                f"Empty input should produce empty output, got:\n{result}"
            )
        else:
            # For non-empty input, check basic markdown table structure
            lines = result.strip().split("\n")

            # Check for at least 3 lines (header, separator, data)
            assert len(lines) >= 3, (
                f"Expected at least 3 lines in output, got {len(lines)}:\n{result}"
            )

            # Check that all lines start and end with pipe
            for i, line in enumerate(lines):
                assert line.startswith("|") and line.endswith("|"), (
                    f"Line {i} should start and end with pipe characters:\n{line}"
                )

            # Verify separator row contains dashes
            assert "-" in lines[1], (
                f"Separator row (line 1) should contain dashes:\n{lines[1]}"
            )


# HTML format tests
class TestHTML:
    """Test cases for HTML formatter."""

    def test_simple_format(self, simple_data):
        """Test HTML formatting with simple data."""
        result = _format_html(simple_data)

        # Check basic structure
        assert "<table>" in result, (
            f"Result should contain a table tag. Got: '{result[:50]}...'"
        )
        assert "</table>" in result, (
            f"Result should contain a closing table tag. Got: '...{result[-50:]}'"
        )

        # Check for table sections
        assert "<thead>" in result, (
            f"Result should contain a thead section. Got truncated: '{result[:100]}...'"
        )
        assert "<tbody>" in result, (
            f"Result should contain a tbody section. Got truncated: '{result[:100]}...'"
        )

        # Check for data presence - more important than exact format
        for key in ["name", "age", "city"]:
            assert f"<th>{key}</th>" in result, f"Expected '<th>{key}</th>' in result"
        assert "<td>Alice</td>" in result, "Expected '<td>Alice</td>' in result"
        assert "<td>New York</td>" in result, "Expected '<td>New York</td>' in result"

    def test_special_characters(self):
        """Test HTML escaping of special characters."""
        data = [{"content": "<script>alert('xss')</script>"}]
        result = _format_html(data)

        # Check that special characters are properly escaped
        assert "<script>" not in result, (
            "HTML should escape script tags, but found unescaped tag"
        )
        assert "&lt;script&gt;" in result, "Expected escaped script tags in result"
        assert "&apos;" in result or "&#x27;" in result or "&#39;" in result, (
            "Expected escaped quote in result"
        )

    def test_missing_values(self, missing_data):
        """Test HTML formatting with missing values."""
        result = _format_html(missing_data)
        # Empty cells should be represented with empty td tags
        assert "<td></td>" in result, "Expected empty cell representation in result"


# CSV format tests
class TestCSV:
    """Test cases for CSV formatter."""

    def test_simple_format(self, simple_data):
        """Test CSV formatting with simple data."""
        result = _format_csv(simple_data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        # Test structure
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

        # Test content - more specific assertions with helpful messages
        assert "name" in rows[0], (
            f"Expected 'name' column in CSV, got columns: {list(rows[0].keys())}"
        )
        assert rows[0]["name"] == "Alice", (
            f"Expected first row name='Alice', got '{rows[0].get('name')}'"
        )
        assert rows[0]["age"] == "30", (
            f"Expected first row age='30', got '{rows[0].get('age')}'"
        )
        assert rows[1]["city"] == "Los Angeles", (
            f"Expected second row city='Los Angeles', got '{rows[1].get('city')}'"
        )

    def test_missing_values(self, missing_data):
        """Test CSV formatting with missing values."""
        result = _format_csv(missing_data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        # Check for missing values - CSV should have empty strings
        assert rows[0].get("city", "NOT_EMPTY") == "", (
            f"Expected empty string for missing city, got '{rows[0].get('city', 'NOT_FOUND')}'"
        )
        assert rows[1].get("age", "NOT_EMPTY") == "", (
            f"Expected empty string for missing age, got '{rows[1].get('age', 'NOT_FOUND')}'"
        )

    def test_complex_types(self, complex_data):
        """Test CSV formatting with complex data types."""
        result = _format_csv(complex_data)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)

        # Check all values are strings (CSV format requirement)
        non_string_values = []
        for i, row in enumerate(rows):
            for key, value in row.items():
                if not isinstance(value, str):
                    non_string_values.append((i, key, type(value)))

        assert not non_string_values, (
            f"Found non-string values in CSV: {non_string_values}"
        )


# XML format tests
class TestXML:
    """Test cases for XML formatter."""

    def test_simple_format(self, simple_data):
        """Test XML formatting with simple data."""
        try:
            result = _format_xml(simple_data)
            root = ET.fromstring(result)
        except ET.ParseError as e:
            pytest.fail(f"XML parsing failed: {e}. XML content: '{result}'")

        # Check structure
        assert root.tag == "items", f"Expected root tag 'items', got '{root.tag}'"
        items = root.findall("item")
        assert len(items) == 2, f"Expected 2 items, got {len(items)}"

        # Check content
        first_item = items[0]
        name_elem = first_item.find("name")
        age_elem = first_item.find("age")

        assert name_elem is not None, "Name element not found in XML"
        assert age_elem is not None, "Age element not found in XML"
        assert name_elem.text == "Alice", (
            f"Expected name='Alice', got '{name_elem.text}'"
        )
        assert age_elem.text == "30", f"Expected age='30', got '{age_elem.text}'"

    def test_special_characters(self):
        """Test XML escaping of special characters."""
        data = [{"content": "<tag>value</tag>"}]
        result = _format_xml(data)

        try:
            root = ET.fromstring(result)
            content_elem = root.find("item/content")
            assert content_elem is not None, "Content element not found in XML"

            # XML parsers normalize content, so we're testing that the content
            # can be parsed and retrieved correctly, not the exact string format
            assert content_elem.text == "<tag>value</tag>", (
                f"Expected original content in parsed XML, got '{content_elem.text}'"
            )

        except ET.ParseError as e:
            pytest.fail(
                f"XML parsing failed with special characters: {e}. XML content: '{result}'"
            )


# Integration tests
def test_format_table_dispatch(simple_data):
    """Test format_table dispatches to correct formatter."""
    formats = ["markdown", "html", "csv", "xml"]
    formatters = [_format_markdown, _format_html, _format_csv, _format_xml]

    for format_name, formatter_func in zip(formats, formatters, strict=False):
        # Compare with direct formatter call
        table_result = format_table(simple_data, format_name)  # type: ignore
        direct_result = formatter_func(simple_data)

        assert table_result == direct_result, (
            f"format_table({format_name}) result doesn't match direct formatter call:\n{table_result}\n\nvs expected:\n{direct_result}"
        )

        # Also verify format-specific characteristics
        if format_name == "markdown":
            assert "|" in table_result, (
                f"Markdown table should contain pipe characters:\n{table_result}"
            )
        elif format_name == "html":
            assert "<table>" in table_result, (
                f"HTML output should contain table tags:\n{table_result[:100]}..."
            )
        elif format_name == "csv":
            assert "," in table_result, (
                f"CSV should contain commas:\n{table_result[:100]}..."
            )
        elif format_name == "xml":
            assert "<items>" in table_result, (
                f"XML should have items root element:\n{table_result[:100]}..."
            )


def test_format_table_invalid_format(simple_data):
    """Test format_table with invalid format."""
    invalid_format = "invalid"  # Using a variable to avoid type checking complaints
    with pytest.raises(ValueError) as exc_info:
        format_table(simple_data, invalid_format)  # type: ignore

    assert "Unsupported format" in str(exc_info.value), (
        f"Expected error message about unsupported format, got: '{exc_info.value}'"
    )
