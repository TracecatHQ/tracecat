"""Default tools available to all agents.

Inspired by: https://docs.cursor.com/chat/tools

- read_file: read contents of a file (up to 250 lines)
- create_file: create a new file
- search_files: find files by name using fuzzy matching
- list_directory: list contents of a directory
- grep_search: search text files for a given pattern
- find_and_replace: find and replace text in a file
- jsonpath_find: given a JSONPath expression, search JSON files for matches
- jsonpath_find_and_replace: given a JSONPath expression, find and replace all matches
- apply_python_lambda: run a Python lambda function (given as a string)
"""

import difflib
import re
from pathlib import Path
from pydantic_ai.tools import Tool

from tracecat.config import TRACECAT__MAX_FILE_SIZE_BYTES
from tracecat_registry.integrations.grep import (
    grep_search,
    jsonpath_find,
    jsonpath_find_and_replace,
)
from tracecat_registry.core.transform import apply


def read_file(
    file_path: str,
) -> str:
    """Read the contents of a text file (up to 250 lines).

    Args:
        file_path: Path to the file to read.

    Returns:
        The contents of the file.
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise ValueError(f"File does not exist: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    # Check file size to prevent DoS
    file_size = path.stat().st_size
    if file_size > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File too large: {file_size} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= 250:  # Limit to 250 lines
                    break
                lines.append(line.rstrip("\n\r"))
            return "\n".join(lines)
    except (OSError, IOError, UnicodeDecodeError) as e:
        raise RuntimeError(f"File read error: {e}")


def search_files(
    query: str,
    dir_path: str,
    max_results: int = 10,
) -> list[str]:
    """Search for files by name using fuzzy matching.

    Args:
        query: Search query to match against file names.
        dir_path: Path to the directory to search.
        max_results: Maximum number of results to return.

    Returns:
        List of file paths.
    """
    path = Path(dir_path).resolve()

    if not path.exists():
        raise ValueError(f"Directory does not exist: {path}")

    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")

    if len(query) > 100:
        raise ValueError("Search query too long (max 100 chars)")

    # Collect all files recursively
    files = []
    try:
        for file_path in path.rglob("*"):
            if file_path.is_file():
                files.append(file_path)
    except (OSError, PermissionError) as e:
        raise RuntimeError(f"Directory traversal error: {e}")

    # Fuzzy match against file names using difflib
    matches = []
    query_lower = query.lower()
    for file_path in files:
        filename_lower = file_path.name.lower()
        # Use sequence matcher for similarity
        similarity = difflib.SequenceMatcher(None, query_lower, filename_lower).ratio()
        # Also check for substring matches
        if query_lower in filename_lower:
            similarity = max(similarity, 0.8)  # Boost substring matches

        if similarity > 0.3:  # Threshold for fuzzy matching
            matches.append((similarity, str(file_path)))

    # Sort by similarity (descending) and return top results
    matches.sort(key=lambda x: x[0], reverse=True)
    return [match[1] for match in matches[:max_results]]


def list_directory(
    dir_path: str,
) -> list[str]:
    """List the contents of a directory.

    Args:
        dir_path: Path to the directory to list.

    Returns:
        List of file paths.
    """
    path = Path(dir_path).resolve()

    if not path.exists():
        raise ValueError(f"Directory does not exist: {path}")

    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    try:
        entries = []
        for entry in path.iterdir():
            if entry.is_file():
                entries.append(f"[FILE] {entry.name}")
            elif entry.is_dir():
                entries.append(f"[DIR]  {entry.name}")
            else:
                entries.append(f"[OTHER] {entry.name}")
        return sorted(entries)
    except (OSError, PermissionError) as e:
        raise RuntimeError(f"Directory listing error: {e}")


def find_and_replace(
    file_path: str,
    pattern: str,
    replacement: str,
) -> str:
    """Find and replace regex pattern matches in a text file.

    Args:
        file_path: Path to the file to find and replace in.
        pattern: Regex pattern to find.
        replacement: Replacement text.

    Returns:
        The contents of the file with the replacements applied.
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise ValueError(f"File does not exist: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    # Validate pattern
    if not pattern or not pattern.strip():
        raise ValueError("Pattern cannot be empty")

    if len(pattern) > 1000:
        raise ValueError("Pattern too long (max 1000 chars)")

    if "\x00" in pattern:
        raise ValueError("Pattern contains null bytes")

    # Validate replacement
    if replacement is None:
        raise ValueError("Replacement cannot be None")

    if len(replacement) > 10000:
        raise ValueError("Replacement too long (max 10000 chars)")

    # Check file size
    file_size = path.stat().st_size
    if file_size > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File too large: {file_size} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
        )

    try:
        # Compile regex pattern
        regex = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}")

    try:
        # Read file content
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Perform replacement
        modified_content = regex.sub(replacement, content)

        # Write back to file
        with open(path, "w", encoding="utf-8") as f:
            f.write(modified_content)

        return modified_content

    except (OSError, IOError, UnicodeDecodeError) as e:
        raise RuntimeError(f"File operation error: {e}")


def create_file(
    file_path: str,
    content: str = "",
) -> str:
    """Create a new file with the specified content.

    Args:
        file_path: Path to the file to create.
        content: Content to write to the file.

    Returns:
        Success message with the file path.
    """
    path = Path(file_path).resolve()

    # Check if file already exists
    if path.exists():
        raise ValueError(f"File already exists: {path}")

    # Validate content length
    if len(content) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"Content too large: {len(content)} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
        )

    # Ensure parent directory exists
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        raise RuntimeError(f"Failed to create parent directory: {e}")

    try:
        # Create and write to file
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"File created successfully: {path}"

    except (OSError, IOError) as e:
        raise RuntimeError(f"File creation error: {e}")


def apply_python_lambda(
    value: str,
    lambda_function: str,
) -> str:
    """Run a Python lambda function, given as a string, on a value.

    Args:
        value: Value to run the lambda function on.
        lambda_function: Python lambda function (e.g. lambda x: x + 1) to run.

    Returns:
        The result of the Python code.
    """
    return apply(value, lambda_function)


_default_tools = [
    Tool(
        name="read_file",
        description="Read the contents of a text file (up to 250 lines).",
        function=read_file,
    ),
    Tool(
        name="create_file",
        description="Create a new file with the specified content.",
        function=create_file,
    ),
    Tool(
        name="search_files",
        description="Search for files by name using fuzzy matching.",
        function=search_files,
    ),
    Tool(
        name="list_directory",
        description="List the contents of a directory.",
        function=list_directory,
    ),
    Tool(
        name="grep_search",
        description="Search for a pattern in a directory using ripgrep.",
        function=grep_search,
    ),
    Tool(
        name="find_and_replace",
        description="Find and replace regex pattern matches in a text file.",
        function=find_and_replace,
    ),
    Tool(
        name="jsonpath_find",
        description="Find matches in a JSON file using a JSONPath expression.",
        function=jsonpath_find,
    ),
    Tool(
        name="jsonpath_find_and_replace",
        description="Find and replace all matches of a JSONPath expression in a file.",
        function=jsonpath_find_and_replace,
    ),
    Tool(
        name="apply_python_lambda",
        description="Run a Python lambda function, given as a string, on a value.",
        function=apply_python_lambda,
    ),
]
