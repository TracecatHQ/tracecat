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


def unescape_string(s: str) -> str:
    """Convert string escape sequences to their actual characters.

    Handles common escape sequences:
    - "\\n" becomes a newline
    - "\\t" becomes a tab
    - "\\r" becomes a carriage return
    - "\\\\" becomes a backslash
    """
    # Use a single regex substitution instead of multiple string replacements
    return re.sub(
        r"\\([\\nrt])",
        lambda m: {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}[m.group(1)],
        s,
    )
