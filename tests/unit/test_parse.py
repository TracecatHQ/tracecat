from pathlib import Path

from tracecat.expressions.common import eval_jsonpath
from tracecat.parse import (
    get_pyproject_toml_required_deps,
    resolve_jsonschema_refs,
    traverse_expressions,
    traverse_leaves,
    unescape_string,
)


def test_iter_dict_leaves():
    # Test case 1: Nested dictionary
    obj1 = d = {
        "a": {"b": {"c": 1}, "d": 2},
        "e": 3,
        "f": [{"g": 4}, {"h": 5}],
        "i": [6, 7],
    }
    expected1 = [
        ("a.b.c", 1),
        ("a.d", 2),
        ("e", 3),
        ("f[0].g", 4),
        ("f[1].h", 5),
        ("i[0]", 6),
        ("i[1]", 7),
    ]
    actual = list(traverse_leaves(obj1))
    assert actual == expected1

    # Test that the jsonpath expressions are valid
    for path, expected_value in actual:
        actual_value = eval_jsonpath(path, d)
        assert actual_value == expected_value


def test_more_iter_dict_leaves():
    # Test case 2: Empty dictionary
    obj2 = {}
    expected2 = []
    assert list(traverse_leaves(obj2)) == expected2

    # Test case 3: Dictionary with empty values
    obj3 = {"a": {}, "b": {"c": {}}, "d": []}
    expected3 = []
    assert list(traverse_leaves(obj3)) == expected3

    # Test case 4: Dictionary with non-dict values
    obj4 = {"a": 1, "b": [2, 3], "c": "hello"}
    expected4 = [("a", 1), ("b[0]", 2), ("b[1]", 3), ("c", "hello")]
    assert list(traverse_leaves(obj4)) == expected4


def test_traverse_expressions():
    # Test case 1: Single expression in string
    data = {
        "test": "Hello, ${{ var.name }}",
    }
    assert list(traverse_expressions(data)) == ["var.name"]

    # Test case 2: Multiple expressions in string
    data = {
        "test": "This is a ${{ 1 }} or ${{ 2 }}",
    }
    assert list(traverse_expressions(data)) == ["1", "2"]

    # Test case 3: Nested expressions in objects and lists
    data = {
        "test": "This is a ${{ 1 }} or ${{ 2 }}",
        "list": [
            "This is a ${{ 3 }} or ${{ 4 }}",
            "second",
            {
                "test": "This is a ${{ 5 }} or ${{ 6 }}",
            },
        ],
        "data": "${{ 7 }}${{ 8 }}",
    }
    assert list(traverse_expressions(data)) == ["1", "2", "3", "4", "5", "6", "7", "8"]

    # Test case 4: No expressions
    data = {
        "test": "Hello world",
        "list": ["no expressions", {"test": 123}],
    }
    assert list(traverse_expressions(data)) == []

    # Test case 5: Empty data structures
    data = {}
    assert list(traverse_expressions(data)) == []
    data = {"test": {}, "list": []}
    assert list(traverse_expressions(data)) == []


def test_parse_pyproject_toml_deps_basic(tmp_path: Path) -> None:
    """Test parsing a basic pyproject.toml with only direct dependencies."""
    # Create a temporary pyproject.toml file
    content = """
[project]
dependencies = [
    "requests>=2.28.0",
    "pydantic~=2.0",
]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)

    # Test parsing
    deps = get_pyproject_toml_required_deps(pyproject)
    assert len(deps) == 2
    assert "requests>=2.28.0" in deps
    assert "pydantic~=2.0" in deps


def test_parse_pyproject_toml_deps_with_optional(tmp_path: Path) -> None:
    """Test parsing a pyproject.toml with both direct and optional dependencies.
    Note: Optional dependencies are not included in the result."""
    content = """
[project]
dependencies = [
    "requests>=2.28.0",
]
[project.optional-dependencies]
test = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
]
dev = [
    "black>=23.0.0",
]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)

    # Test parsing - should only include direct dependencies
    deps = get_pyproject_toml_required_deps(pyproject)
    assert len(deps) == 1
    assert "requests>=2.28.0" in deps


def test_parse_pyproject_toml_deps_empty_project(tmp_path: Path) -> None:
    """Test parsing a pyproject.toml with no dependencies."""
    content = """
[project]
name = "test-project"
version = "0.1.0"
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)

    # Test parsing
    deps = get_pyproject_toml_required_deps(pyproject)
    assert len(deps) == 0


def test_parse_pyproject_toml_deps_missing_file(tmp_path: Path) -> None:
    """Test handling of missing pyproject.toml file."""
    nonexistent_file = tmp_path / "nonexistent.toml"
    deps = get_pyproject_toml_required_deps(nonexistent_file)
    assert deps == []


def test_parse_pyproject_toml_deps_invalid_toml(tmp_path: Path) -> None:
    """Test handling of invalid TOML content."""
    content = """
[project
invalid toml content
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)

    # Test parsing
    deps = get_pyproject_toml_required_deps(pyproject)
    assert deps == []


def test_unescape_string_newlines() -> None:
    """Test that backslash-n sequences are converted to actual newlines."""
    input_str = "Hello\\nWorld"
    expected = "Hello\nWorld"
    assert unescape_string(input_str) == expected


def test_unescape_string_tabs() -> None:
    """Test that backslash-t sequences are converted to actual tabs."""
    input_str = "Hello\\tWorld"
    expected = "Hello\tWorld"
    assert unescape_string(input_str) == expected


def test_unescape_string_carriage_returns() -> None:
    """Test that backslash-r sequences are converted to actual carriage returns."""
    input_str = "Hello\\rWorld"
    expected = "Hello\rWorld"
    assert unescape_string(input_str) == expected


def test_unescape_string_backslashes() -> None:
    """Test that double backslashes are converted to a single backslash."""
    input_str = "Hello\\\\World"
    expected = "Hello\\World"
    assert unescape_string(input_str) == expected


def test_unescape_string_multiple_escapes() -> None:
    """Test that multiple escape sequences in a string are all converted."""
    input_str = "Line1\\nLine2\\tTabbed\\r\\\\Backslash"
    expected = "Line1\nLine2\tTabbed\r\\Backslash"
    assert unescape_string(input_str) == expected


def test_unescape_string_no_escapes() -> None:
    """Test that strings without escape sequences remain unchanged."""
    input_str = "Regular string with no escapes"
    assert unescape_string(input_str) == input_str


def test_unescape_string_empty() -> None:
    """Test that empty strings are handled correctly."""
    assert unescape_string("") == ""


def test_resolve_jsonschema_refs_basic():
    """Test basic reference resolution in a JSON schema."""
    schema = {
        "$defs": {
            "person": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            }
        },
        "properties": {"employee": {"$ref": "#/$defs/person"}},
    }

    resolved = resolve_jsonschema_refs(schema)

    # Check that $defs is no longer in the resolved schema
    assert "$defs" not in resolved

    # Check that the reference was properly resolved
    assert resolved["properties"]["employee"]["type"] == "object"
    assert "name" in resolved["properties"]["employee"]["properties"]
    assert "age" in resolved["properties"]["employee"]["properties"]
    assert resolved["properties"]["employee"]["properties"]["name"]["type"] == "string"
    assert resolved["properties"]["employee"]["properties"]["age"]["type"] == "integer"


def test_resolve_jsonschema_refs_with_additional_props():
    """Test reference resolution when the reference property has additional fields."""
    schema = {
        "$defs": {
            "person": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
            }
        },
        "properties": {
            "employee": {
                "$ref": "#/$defs/person",
                "description": "An employee record",
                "required": True,
            }
        },
    }

    resolved = resolve_jsonschema_refs(schema)

    # Check that additional properties are preserved
    assert resolved["properties"]["employee"]["type"] == "object"
    assert resolved["properties"]["employee"]["description"] == "An employee record"
    assert resolved["properties"]["employee"]["required"] is True
    assert "name" in resolved["properties"]["employee"]["properties"]


def test_resolve_jsonschema_refs_with_missing_ref():
    """Test handling of references to undefined definitions."""
    schema = {
        "$defs": {
            "person": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
            }
        },
        "properties": {"employee": {"$ref": "#/$defs/nonexistent"}},
    }

    resolved = resolve_jsonschema_refs(schema)

    # The reference should remain unresolved since the definition doesn't exist
    assert resolved["properties"]["employee"]["$ref"] == "#/$defs/nonexistent"


def test_resolve_jsonschema_refs_with_external_refs():
    """Test that external references are not resolved."""
    schema = {
        "properties": {"employee": {"$ref": "https://example.com/schemas/person.json"}}
    }

    resolved = resolve_jsonschema_refs(schema)

    # External references should remain unchanged
    assert (
        resolved["properties"]["employee"]["$ref"]
        == "https://example.com/schemas/person.json"
    )


def test_resolve_jsonschema_refs_with_no_refs():
    """Test a schema with no references to resolve."""
    schema = {
        "properties": {
            "employee": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            }
        }
    }

    resolved = resolve_jsonschema_refs(schema)

    # The schema should remain unchanged
    assert resolved == schema


def test_resolve_jsonschema_refs_nested_properties():
    """Test reference resolution in nested properties."""
    schema = {
        "$defs": {
            "address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
            }
        },
        "properties": {
            "person": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "home_address": {"$ref": "#/$defs/address"},
                },
            }
        },
    }

    # The current implementation doesn't resolve nested references
    # This test documents this limitation
    resolved = resolve_jsonschema_refs(schema)

    # The top-level properties are processed, but nested refs remain
    assert "$defs" not in resolved
    assert "person" in resolved["properties"]
    # The nested reference remains unresolved
    assert (
        resolved["properties"]["person"]["properties"]["home_address"]["$ref"]
        == "#/$defs/address"
    )
