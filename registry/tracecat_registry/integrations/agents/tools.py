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

import orjson
import jsonpath_ng
import difflib
import re
from pathlib import Path
from typing import Any
import jsonpath_ng.exceptions
from pydantic_ai import ModelRetry
from pydantic_ai.tools import Tool

from tracecat.config import TRACECAT__MAX_FILE_SIZE_BYTES
from tracecat_registry.integrations.grep import (
    grep_search as _grep_search,
    jsonpath_find as _jsonpath_find,
    jsonpath_find_and_replace as _jsonpath_find_and_replace,
)
from tracecat_registry.core.transform import apply


def _is_text_file(file_path: Path) -> bool:
    """Check if a file is likely a text file by examining its content."""
    try:
        # Read first 8192 bytes to check for binary content
        with open(file_path, "rb") as f:
            chunk = f.read(8192)

        # Check for null bytes (common in binary files)
        if b"\x00" in chunk:
            return False

        # Try to decode as UTF-8
        try:
            chunk.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    except (OSError, IOError):
        return False


def create_secure_file_tools(temp_dir: str) -> list[Tool]:
    """Create secure file tools that are restricted to the given temporary directory.

    Args:
        temp_dir: The temporary directory to restrict all file operations to.

    Returns:
        List of secure file tools with temp_dir pre-bound.
    """
    temp_path = Path(temp_dir).resolve()

    def _validate_and_resolve_path(
        file_path: str, *, must_exist: bool = False, must_be_file: bool = False
    ) -> Path:
        """Validate and resolve a file path within the temp directory.

        Args:
            file_path: The file path to validate
            must_exist: Whether the path must exist
            must_be_file: Whether the path must be a file (only checked if must_exist=True)

        Returns:
            Resolved path within temp directory

        Raises:
            ValueError: If path validation fails
        """
        # Security: Validate input
        if not file_path or not isinstance(file_path, str):
            raise ValueError("File path must be a non-empty string")

        if len(file_path) > 1000:
            raise ValueError("File path too long (max 1000 chars)")

        # Security: Check for null bytes
        if "\x00" in file_path:
            raise ValueError("File path contains null bytes")

        # Security: Only allow relative paths
        if Path(file_path).is_absolute():
            raise ValueError("Absolute paths are not allowed. Use relative paths only.")

        # Security: Prevent directory traversal
        if ".." in file_path or file_path.startswith("/") or file_path.startswith("\\"):
            raise ValueError("Directory traversal is not allowed.")

        # Security: Prevent hidden files (except current directory)
        if file_path != "." and (
            file_path.startswith(".")
            or any(part.startswith(".") for part in Path(file_path).parts)
        ):
            raise ValueError("Hidden files are not allowed.")

        # Security: Prevent Windows device names
        path_parts = Path(file_path).parts
        windows_devices = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        }
        for part in path_parts:
            if part.upper() in windows_devices:
                raise ValueError(f"Windows device name not allowed: {part}")

        # Construct secure path within temp directory
        full_path = temp_path / file_path

        # Security: Resolve path and check it's still within temp directory
        try:
            resolved_path = full_path.resolve()
        except (OSError, RuntimeError) as e:
            raise ValueError(f"Path resolution failed: {e}")

        if not str(resolved_path).startswith(str(temp_path)):
            raise ValueError("Path escapes temporary directory")

        # Security: Check for symlinks that could point outside temp directory
        if resolved_path.is_symlink():
            symlink_target = resolved_path.readlink()
            if symlink_target.is_absolute() or ".." in str(symlink_target):
                raise ValueError(
                    "Symbolic links outside temp directory are not allowed"
                )

        # Existence and type checks
        if must_exist:
            if not resolved_path.exists():
                raise ValueError(f"Path does not exist: {file_path}")

            if must_be_file and not resolved_path.is_file():
                raise ValueError(f"Path is not a file: {file_path}")

        return resolved_path

    def _check_directory_limits(
        dir_path: Path, max_depth: int = 10, max_files: int = 1000
    ) -> None:
        """Check directory depth and file count limits to prevent resource exhaustion."""
        depth = len(dir_path.relative_to(temp_path).parts)
        if depth > max_depth:
            raise ValueError(f"Directory depth exceeds limit: {depth} > {max_depth}")

        try:
            file_count = sum(1 for _ in dir_path.rglob("*") if _.is_file())
            if file_count > max_files:
                raise ValueError(
                    f"Directory contains too many files: {file_count} > {max_files}"
                )
        except (OSError, PermissionError) as e:
            raise RuntimeError(f"Directory traversal error: {e}")

    # Secure read_file with temp_dir pre-bound
    def read_file(file_path: str) -> str:
        """Read the contents of a text file (up to 250 lines) within the temp directory.

        Args:
            file_path: Relative path to the file to read.

        Returns:
            The contents of the file.
        """
        path = _validate_and_resolve_path(file_path, must_exist=True, must_be_file=True)

        # Security: Check if file is likely a text file
        if not _is_text_file(path):
            raise ValueError("File appears to be binary and cannot be read as text")

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

    # Secure create_file with temp_dir pre-bound
    def create_file(file_path: str, content: str = "") -> str:
        """Create a new file with the specified content in the temp directory.

        Args:
            file_path: Relative path to the file to create.
            content: Content to write to the file.

        Returns:
            Success message with the file path.
        """
        path = _validate_and_resolve_path(file_path)

        # Check if file already exists
        if path.exists():
            raise ValueError(f"File already exists: {file_path}")

        # Validate content
        if not isinstance(content, str):
            raise ValueError("Content must be a string")

        if len(content) > TRACECAT__MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"Content too large: {len(content)} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
            )

        # Security: Check for null bytes in content
        if "\x00" in content:
            raise ValueError("Content contains null bytes")

        # Ensure parent directory exists and check limits
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _check_directory_limits(path.parent)
        except (OSError, PermissionError) as e:
            raise RuntimeError(f"Failed to create parent directory: {e}")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"File created successfully: {file_path}"
        except (OSError, IOError) as e:
            raise RuntimeError(f"File creation error: {e}")

    # Secure search_files with temp_dir pre-bound
    def search_files(query: str, max_results: int = 10) -> list[str]:
        """Search for files by name using fuzzy matching within the temp directory.

        Args:
            query: Search query to match against file names.
            max_results: Maximum number of results to return.

        Returns:
            List of relative file paths.
        """
        if not query or not isinstance(query, str) or not query.strip():
            raise ValueError("Search query must be a non-empty string")

        if len(query) > 100:
            raise ValueError("Search query too long (max 100 chars)")

        # Security: Check for null bytes
        if "\x00" in query:
            raise ValueError("Search query contains null bytes")

        # Check directory limits
        _check_directory_limits(temp_path)

        # Search only within temp directory
        files = []
        try:
            for file_path in temp_path.rglob("*"):
                if file_path.is_file():
                    # Return relative path from temp_dir
                    rel_path = file_path.relative_to(temp_path)
                    files.append(rel_path)
        except (OSError, PermissionError) as e:
            raise RuntimeError(f"Directory traversal error: {e}")

        # Fuzzy match against file names
        matches = []
        query_lower = query.lower()
        for file_path in files:
            filename_lower = file_path.name.lower()
            similarity = difflib.SequenceMatcher(
                None, query_lower, filename_lower
            ).ratio()
            if query_lower in filename_lower:
                similarity = max(similarity, 0.8)

            if similarity > 0.3:
                matches.append((similarity, str(file_path)))

        matches.sort(key=lambda x: x[0], reverse=True)
        return [match[1] for match in matches[:max_results]]

    # Secure list_directory with temp_dir pre-bound
    def list_directory(dir_path: str = ".") -> list[str]:
        """List the contents of a directory within the temp directory.

        Args:
            dir_path: Relative path to the directory to list (defaults to temp root).

        Returns:
            List of directory entries with type prefixes.
        """
        path = _validate_and_resolve_path(dir_path, must_exist=True)

        if not path.is_dir():
            raise ValueError(f"Path is not a directory: {dir_path}")

        # Check directory limits
        _check_directory_limits(path)

        try:
            entries = []
            for entry in path.iterdir():
                if entry.is_file():
                    entries.append(f"[FILE] {entry.name}")
                elif entry.is_dir():
                    entries.append(f"[DIR]  {entry.name}")
                elif entry.is_symlink():
                    entries.append(f"[LINK] {entry.name}")
                else:
                    entries.append(f"[OTHER] {entry.name}")
            return sorted(entries)
        except (OSError, PermissionError) as e:
            raise RuntimeError(f"Directory listing error: {e}")

    # Secure find_and_replace with temp_dir pre-bound
    def find_and_replace(file_path: str, pattern: str, replacement: str) -> str:
        """Find and replace regex pattern matches in a text file within temp directory.

        Args:
            file_path: Relative path to the file to modify.
            pattern: Regex pattern to find.
            replacement: Replacement text.

        Returns:
            The contents of the file with the replacements applied.
        """
        path = _validate_and_resolve_path(file_path, must_exist=True, must_be_file=True)

        # Security: Check if file is likely a text file
        if not _is_text_file(path):
            raise ValueError("File appears to be binary and cannot be modified as text")

        # Validate pattern
        if not pattern or not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("Pattern must be a non-empty string")

        if len(pattern) > 1000:
            raise ValueError("Pattern too long (max 1000 chars)")

        if "\x00" in pattern:
            raise ValueError("Pattern contains null bytes")

        # Validate replacement
        if replacement is None:
            raise ValueError("Replacement cannot be None")

        if not isinstance(replacement, str):
            raise ValueError("Replacement must be a string")

        if len(replacement) > 10000:
            raise ValueError("Replacement too long (max 10000 chars)")

        if "\x00" in replacement:
            raise ValueError("Replacement contains null bytes")

        # Check file size
        file_size = path.stat().st_size
        if file_size > TRACECAT__MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"File too large: {file_size} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
            )

        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            modified_content = regex.sub(replacement, content)

            # Check modified content size
            if len(modified_content) > TRACECAT__MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    f"Modified content too large: {len(modified_content)} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
                )

            with open(path, "w", encoding="utf-8") as f:
                f.write(modified_content)

            return modified_content
        except (OSError, IOError, UnicodeDecodeError) as e:
            raise RuntimeError(f"File operation error: {e}")

    # Secure grep_search with temp_dir pre-bound
    def grep_search(
        pattern: str, max_columns: int | None = None
    ) -> str | list[str] | dict[str, Any] | list[dict[str, Any]]:
        """Search for a pattern in files within the temp directory.

        Args:
            pattern: Pattern to search for.
            max_columns: Maximum number of columns to grep.

        Returns:
            List of search results.
        """
        if not pattern or not isinstance(pattern, str):
            raise ValueError("Pattern must be a non-empty string")

        if "\x00" in pattern:
            raise ValueError("Pattern contains null bytes")

        # Check directory limits
        _check_directory_limits(temp_path)

        try:
            return _grep_search(pattern, temp_path, max_columns)
        except Exception as e:
            raise ModelRetry(f"Grep search failed: {e}") from e

    # Secure jsonpath tools with temp_dir pre-bound
    def jsonpath_find(file_path: str, jsonpath_expr: str) -> list[Any]:
        """Find matches in a JSON file using JSONPath within temp directory.

        Args:
            file_path: Relative path to the JSON file.
            jsonpath_expr: JSONPath expression to search for.

        Returns:
            List of matching values.
        """
        path = _validate_and_resolve_path(file_path, must_exist=True, must_be_file=True)

        if not jsonpath_expr or not isinstance(jsonpath_expr, str):
            raise ValueError("JSONPath expression must be a non-empty string")

        if "\x00" in jsonpath_expr:
            raise ValueError("JSONPath expression contains null bytes")

        try:
            return _jsonpath_find(jsonpath_expr, path)
        except orjson.JSONDecodeError as e:
            raise ModelRetry(f"Invalid JSON content: {e}") from e
        except jsonpath_ng.exceptions.JsonPathParserError as e:
            raise ModelRetry(f"Unable to parse JSONPath expression: {e}") from e

    def jsonpath_find_and_replace(
        file_path: str,
        jsonpath_expr: str,
        replacement_value: str | int | float | bool | list[Any] | dict[str, Any] | None,
    ) -> str:
        """Find and replace JSONPath matches in a file within temp directory.

        Args:
            file_path: Relative path to the JSON file.
            jsonpath_expr: JSONPath expression to search for.
            replacement_value: Value to replace matches with.

        Returns:
            Success message.
        """
        path = _validate_and_resolve_path(file_path, must_exist=True, must_be_file=True)

        if not jsonpath_expr or not isinstance(jsonpath_expr, str):
            raise ValueError("JSONPath expression must be a non-empty string")

        if "\x00" in jsonpath_expr:
            raise ValueError("JSONPath expression contains null bytes")

        try:
            return _jsonpath_find_and_replace(jsonpath_expr, path, replacement_value)
        except orjson.JSONDecodeError as e:
            raise ModelRetry(f"Invalid JSON content: {e}") from e
        except jsonpath_ng.exceptions.JsonPathParserError as e:
            raise ModelRetry(f"Unable to parse JSONPath expression: {e}") from e

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

    # Return secure tools
    return [
        Tool(
            name="read_file",
            description="Read the contents of a text file (up to 250 lines) within the temporary directory.",
            function=read_file,
        ),
        Tool(
            name="create_file",
            description="Create a new file with the specified content within the temporary directory.",
            function=create_file,
        ),
        Tool(
            name="search_files",
            description="Search for files by name using fuzzy matching within the temporary directory.",
            function=search_files,
        ),
        Tool(
            name="list_directory",
            description="List the contents of a directory within the temporary directory.",
            function=list_directory,
        ),
        Tool(
            name="grep_search",
            description="Search for a pattern in files within the temporary directory.",
            function=grep_search,
        ),
        Tool(
            name="find_and_replace",
            description="Find and replace regex pattern matches in a text file within the temporary directory.",
            function=find_and_replace,
        ),
        Tool(
            name="jsonpath_find",
            description="Find matches in a JSON file using JSONPath within the temporary directory.",
            function=jsonpath_find,
        ),
        Tool(
            name="jsonpath_find_and_replace",
            description="Find and replace JSONPath matches in a file within the temporary directory.",
            function=jsonpath_find_and_replace,
        ),
        Tool(
            name="apply_python_lambda",
            description="Run a Python lambda function, given as a string, on a value.",
            function=apply_python_lambda,
        ),
    ]


def generate_default_tools_prompt(files: dict[str, str] | None = None) -> str:
    """Generate the default tools prompt with file context using XML structure."""

    if files:
        # Generate file list
        file_list = "\n".join(f"- `{file_path}`" for file_path in files.keys())

        return f"""<file_interaction_guidelines>
You have access to a temporary file environment with the following files:
{file_list}

When the user requests file modifications, use the file modification tools to actually change the files. Modified files will be returned as output.

<tool_requirements>
1. Use tools to interact with files rather than making assumptions about their contents:
   - `read_file` - Read file contents (up to 250 lines)
   - `list_directory` - List directory contents
   - `search_files` - Find files by name using fuzzy matching
   - `grep_search` - Search for patterns across files
   - `find_and_replace` - Modify existing files
   - `create_file` - Create new files
   - `jsonpath_find` - Search JSON files using JSONPath expressions
   - `jsonpath_find_and_replace` - Modify JSON files
   - `apply_python_lambda` - Transform data with Python functions

2. For file modifications:
   - Use `find_and_replace` to change existing files
   - Use `create_file` to create new files
   - Use `jsonpath_find_and_replace` for JSON modifications

3. Follow this workflow:
   - Explore structure with `list_directory` and `read_file`
   - Use `grep_search` to find relevant patterns before making changes
   - Make targeted modifications with `find_and_replace`
   - Verify changes with `read_file` or `grep_search`
   - Work incrementally with small, focused changes
</tool_requirements>

<best_practices>
- Use `grep_search` to find function definitions, imports, or specific patterns
- Use `jsonpath_find` for JSON/YAML files, `grep_search` for other formats
- Use `apply_python_lambda` to process and transform file contents
- Use `search_files` when you need to locate files by partial names
- Verify changes with `read_file` after modifications
</best_practices>
</file_interaction_guidelines>"""
    else:
        return """<file_interaction_guidelines>
You have access to file manipulation tools:
- `read_file` - Read file contents
- `create_file` - Create new files
- `search_files` - Find files by name
- `list_directory` - List directory contents
- `grep_search` - Search for patterns in files
- `find_and_replace` - Modify file contents
- `jsonpath_find` - Query JSON files
- `apply_python_lambda` - Transform data

When working with files, use these tools to interact with the file system directly.
</file_interaction_guidelines>"""
