from pathlib import Path

import pytest

from tracecat.expressions.common import eval_jsonpath
from tracecat.parse import (
    get_pyproject_toml_required_deps,
    to_flat_jsonpaths,
    traverse_expressions,
    traverse_leaves,
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


def test_simple_json_paths() -> None:
    """Test extraction of JSON paths from a simple flat object."""
    input_json = {"name": "John", "age": 30, "active": True}
    expected = {"$.name": "John", "$.age": 30, "$.active": True}
    assert to_flat_jsonpaths(input_json) == expected


def test_nested_json_paths() -> None:
    """Test extraction of JSON paths from nested objects."""
    input_json = {
        "person": {
            "name": "John",
            "address": {"street": "123 Main St", "city": "Boston"},
        }
    }
    expected = {
        "$.person.name": "John",
        "$.person.address.street": "123 Main St",
        "$.person.address.city": "Boston",
    }
    assert to_flat_jsonpaths(input_json) == expected


def test_array_json_paths() -> None:
    """Test extraction of JSON paths from objects containing arrays."""
    input_json = {"users": [{"id": 1, "name": "John"}, {"id": 2, "name": "Jane"}]}
    expected = {
        "$.users[0].id": 1,
        "$.users[0].name": "John",
        "$.users[1].id": 2,
        "$.users[1].name": "Jane",
    }
    assert to_flat_jsonpaths(input_json) == expected


def test_root_array_json_paths() -> None:
    """Test extraction of JSON paths from a root array."""
    input_json = [
        {"id": 1, "name": "John"},
        {"id": 2, "name": "Jane"},
        "simple_string",
        [1, 2, 3],
    ]
    expected = {
        "$[0].id": 1,
        "$[0].name": "John",
        "$[1].id": 2,
        "$[1].name": "Jane",
        "$[2]": "simple_string",
        "$[3][0]": 1,
        "$[3][1]": 2,
        "$[3][2]": 3,
    }
    assert to_flat_jsonpaths(input_json) == expected


def test_dotted_key_json_paths() -> None:
    """Test extraction of JSON paths from objects with dots in key names."""
    input_json = {
        "user.info": {"personal.details": {"first.name": "John", "last.name": "Doe"}},
        "system.metadata": True,
    }
    expected = {
        '$["user.info"]["personal.details"]["first.name"]': "John",
        '$["user.info"]["personal.details"]["last.name"]': "Doe",
        '$["system.metadata"]': True,
    }
    assert to_flat_jsonpaths(input_json) == expected


def test_mixed_notation_json_paths() -> None:
    """Test extraction of JSON paths from objects with mixed notation needs."""
    input_json = {
        "normal": {"dot.field": {"nested": "value", "other.nested": "value2"}},
        "array.field": [{"id.number": 1}],
    }
    expected = {
        '$.normal["dot.field"].nested': "value",
        '$.normal["dot.field"]["other.nested"]': "value2",
        '$["array.field"][0]["id.number"]': 1,
    }
    assert to_flat_jsonpaths(input_json) == expected


def test_special_characters_json_paths() -> None:
    """Test extraction of JSON paths from objects with special characters in keys."""
    input_json = {
        "": "empty",  # Empty string
        "user name": "John",  # Space
        "user-name": "Jane",  # Hyphen
        "@metadata": True,  # Symbol
        "123": "numeric",  # Numeric string
        "user\\name": "backslash",  # Backslash
        'user"name': "quote",  # Quote
        "użytkownik": "unicode",  # Unicode
        "user@123": "mixed",  # Mixed special chars
    }
    expected = {
        '$[""]': "empty",
        '$["user name"]': "John",
        '$["user-name"]': "Jane",
        '$["@metadata"]': True,
        '$["123"]': "numeric",
        '$["user\\\\name"]': "backslash",
        '$["user\\"name"]': "quote",
        '$["użytkownik"]': "unicode",
        '$["user@123"]': "mixed",
    }
    assert to_flat_jsonpaths(input_json) == expected


def test_empty_object() -> None:
    """Test handling of empty objects."""
    assert to_flat_jsonpaths({}) == {}


def test_empty_array() -> None:
    """Test handling of empty arrays."""
    assert to_flat_jsonpaths([]) == {}


def test_none_values() -> None:
    """Test handling of None values in objects."""
    input_json = {"field1": None, "nested": {"field2": None}}
    expected = {"$.field1": None, "$.nested.field2": None}
    assert to_flat_jsonpaths(input_json) == expected


def test_invalid_input() -> None:
    """Test handling of invalid input types."""
    with pytest.raises(TypeError, match="Input must be a dictionary or list"):
        to_flat_jsonpaths("not a dict")  # type: ignore
