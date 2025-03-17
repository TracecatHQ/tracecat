"""Format list of dictionaries into various string representations.

Supported formats:
- csv
- markdown
- html
- xml
"""

import csv
import io
import xml.dom.minidom
import xml.etree.ElementTree as ET
from html import escape
from typing import Any, Literal


def _format_markdown(x: list[dict[str, Any]]) -> str:
    """Format list of dictionaries into markdown table."""
    if not x:
        return ""

    # Get all possible keys from all dictionaries
    all_keys = set()
    for item in x:
        all_keys.update(item.keys())
    all_keys = sorted(all_keys)

    # Create header row
    header = "| " + " | ".join(all_keys) + " |"
    separator = "| " + " | ".join(["---" for _ in all_keys]) + " |"

    # Create data rows
    rows = []
    for item in x:
        row_values = [str(item.get(key, "")) for key in all_keys]
        rows.append("| " + " | ".join(row_values) + " |")

    return "\n".join([header, separator] + rows)


def _format_html(x: list[dict[str, Any]]) -> str:
    """Format list of dictionaries into html table."""
    if not x:
        return ""

    # Get all possible keys from all dictionaries
    all_keys = set()
    for item in x:
        all_keys.update(item.keys())
    all_keys = sorted(all_keys)

    # Create HTML
    result = ["<table>"]

    # Header
    result.append("  <thead>")
    result.append("    <tr>")
    for key in all_keys:
        result.append(f"      <th>{escape(str(key))}</th>")
    result.append("    </tr>")
    result.append("  </thead>")

    # Body
    result.append("  <tbody>")
    for item in x:
        result.append("    <tr>")
        for key in all_keys:
            value = item.get(key, "")
            result.append(f"      <td>{escape(str(value))}</td>")
        result.append("    </tr>")
    result.append("  </tbody>")

    result.append("</table>")
    return "\n".join(result)


def _format_csv(x: list[dict[str, Any]]) -> str:
    """Format list of dictionaries into csv table."""
    if not x:
        return ""

    # Get all possible keys from all dictionaries
    all_keys = set()
    for item in x:
        all_keys.update(item.keys())
    all_keys = sorted(all_keys)

    # Use StringIO to build CSV string
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_keys)

    # Write header and rows
    writer.writeheader()

    # Fill in missing values with None
    normalized_rows = []
    for item in x:
        normalized_row = {key: item.get(key, None) for key in all_keys}
        normalized_rows.append(normalized_row)

    writer.writerows(normalized_rows)

    return output.getvalue()


def _format_xml(x: list[dict[str, Any]]) -> str:
    """Format list of dictionaries into xml table."""
    if not x:
        return ""

    # Create root element
    root = ET.Element("items")

    for item in x:
        # Create an item element for each dictionary
        item_element = ET.SubElement(root, "item")

        # Add key-value pairs as subelements
        for key, value in item.items():
            # Convert value to string and handle None
            value_str = "" if value is None else str(value)

            # Create a subelement with the key name
            key_element = ET.SubElement(item_element, key)
            key_element.text = value_str

    # Convert to string with pretty printing
    rough_string = ET.tostring(root, "utf-8")
    reparsed = xml.dom.minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")


def format_table(
    x: list[dict[str, Any]], format: Literal["markdown", "html", "csv", "xml"]
) -> str:
    """Format list of dictionaries into various string representations:
    - csv
    - markdown
    - html
    - xml

    Args:
        x: List of dictionaries to format
        format: Output format (markdown, html, csv, or xml)

    Returns:
        Formatted string representation of the data
    """
    if format == "markdown":
        return _format_markdown(x)
    elif format == "html":
        return _format_html(x)
    elif format == "csv":
        return _format_csv(x)
    elif format == "xml":
        return _format_xml(x)
    else:
        raise ValueError(f"Unsupported format: {format}")
