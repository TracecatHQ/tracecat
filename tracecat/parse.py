import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import tomli

from tracecat.expressions import patterns
from tracecat.logger import logger


def insert_obj_by_path(
    obj: dict[str, Any], *, path: str, value: Any, sep: str = "."
) -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value


def reconstruct_obj(flat_kv: dict[str, Any], *, sep: str = ".") -> dict[str, Any]:
    """Parse a flat key-value dictionary into a nested dictionary.

    Keys are expected to be delimiter-separated paths.
    """
    obj = {}
    for path, value in flat_kv.items():
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        insert_obj_by_path(obj, path=path, value=value, sep=sep)
    return obj


def traverse_leaves(obj: Any, parent_key: str = "") -> Iterator[tuple[str, Any]]:
    """Iterate through the leaves of a nested dictionary.

    Each iterations returns the location (jsonpath) and the value.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_key = f"{parent_key}.{key}" if parent_key else key
            yield from traverse_leaves(value, new_key)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_key = f"{parent_key}[{i}]"
            yield from traverse_leaves(item, new_key)
    else:
        yield parent_key, obj


def traverse_expressions(obj: Any) -> Iterator[str]:
    """Return an iterator of all expressions in a nested object."""
    for _, value in traverse_leaves(obj):
        if not isinstance(value, str):
            continue
        for match in re.finditer(patterns.TEMPLATE_STRING, value):
            if expr := match.group("expr"):
                yield expr


def safe_url(url: str) -> str:
    """Remove credentials from a url."""
    url_obj = urlparse(url)
    # XXX(safety): Reconstruct url without credentials.
    # Note that we do not recommend passing credentials in the url.
    cleaned_url = urlunparse((url_obj.scheme, url_obj.netloc, url_obj.path, "", "", ""))
    return cleaned_url


def get_pyproject_toml_required_deps(pyproject_path: Path) -> list[str]:
    """Parse pyproject.toml to extract dependencies."""
    try:
        with pyproject_path.open("rb") as f:
            pyproject = tomli.load(f)

        # Get dependencies from pyproject.toml
        project = pyproject.get("project", {})
        return cast(list[str], project.get("dependencies", []))
    except Exception as e:
        logger.error("Error parsing pyproject.toml", error=e)
        return []


def _needs_bracket_notation(key: str) -> bool:
    """
    Determine if a key needs to be wrapped in bracket notation.

    Args:
        key: The key to check

    Returns:
        bool: True if the key needs bracket notation, False otherwise
    """
    # If it's empty string
    if not key:
        return True

    # If it contains any special characters or non-ASCII characters
    special_chars_pattern = r"[^\w]|[^\x00-\x7F]"
    if re.search(special_chars_pattern, key):
        return True

    # If it's a number or starts with a number
    if key.isdigit() or (key and key[0].isdigit()):
        return True

    return False


def _build_path_segment(key: str | int) -> str:
    """
    Build a path segment with proper notation based on the key type and content.

    Args:
        key: The key to build a path segment for

    Returns:
        str: The properly formatted path segment
    """
    if isinstance(key, int):
        return f"[{key}]"

    key_str = str(key)

    if _needs_bracket_notation(key_str):
        # First escape backslashes, then escape quotes
        escaped_key = key_str.replace("\\", "\\\\").replace('"', '\\"')
        return f'["{escaped_key}"]'

    return f".{key_str}"


def _flatten_json(
    obj: dict[str, Any] | list[Any],
    path: str = "$",
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Recursively flatten a JSON object or array into a dictionary mapping JSONPath strings to values.

    Args:
        obj: The object or array to flatten
        path: The current path in the JSON structure
        result: Dictionary to store path-value mappings (used for recursion)

    Returns:
        Dictionary mapping JSONPath strings to their corresponding values
    """
    if result is None:
        result = {}

    if isinstance(obj, dict):
        if not obj:  # Empty dict
            if path != "$":  # Don't add empty root
                result[path] = {}
        for key, value in obj.items():
            # For root level, don't add an extra dot
            if path == "$" and not _needs_bracket_notation(str(key)):
                new_path = f"{path}.{key}"
            else:
                segment = _build_path_segment(key)
                # If segment already starts with [, don't add a dot
                new_path = (
                    f"{path}{segment}"
                    if segment.startswith("[")
                    else f"{path}{segment}"
                )

            if isinstance(value, dict | list) and value:  # Non-empty dict/list
                _flatten_json(value, new_path, result)
            else:
                result[new_path] = value
    elif isinstance(obj, list):
        if not obj:  # Empty list
            if path != "$":  # Don't add empty root
                result[path] = []
        for i, value in enumerate(obj):
            new_path = f"{path}[{i}]"
            if isinstance(value, dict | list) and value:  # Non-empty dict/list
                _flatten_json(value, new_path, result)
            else:
                result[new_path] = value
    return result


def to_flat_jsonpaths(obj: Any) -> dict[str, Any]:
    """
    Extract all JSONPath expressions and their values from a JSON object.

    Args:
        obj: The JSON object to process

    Returns:
        Dictionary mapping JSONPath strings to their corresponding values

    Raises:
        TypeError: If input is not a dictionary or list
    """
    if not isinstance(obj, dict | list):
        raise TypeError("Input must be a dictionary or list")

    return _flatten_json(obj)
