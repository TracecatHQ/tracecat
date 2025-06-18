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
    base_dir: str = ".",
) -> str:
    """Create a new file with the specified content in the base directory.

    Args:
        file_path: Relative path to the file to create.
        content: Content to write to the file.
        base_dir: Base directory to create the file in (fixed by agent).

    Returns:
        Success message with the file path.
    """
    # Security: Only allow relative paths, no absolute paths
    if Path(file_path).is_absolute():
        raise ValueError("Absolute paths are not allowed. Use relative paths only.")

    # Security: Prevent directory traversal attacks
    if ".." in file_path or file_path.startswith("/"):
        raise ValueError(
            "Directory traversal is not allowed. Use simple relative paths only."
        )

    # Security: Prevent hidden files or system files
    if file_path.startswith(".") or any(
        part.startswith(".") for part in Path(file_path).parts
    ):
        raise ValueError("Hidden files are not allowed.")

    # Construct secure path within base directory
    base_path = Path(base_dir).resolve()
    full_path = base_path / file_path

    # Double-check the resolved path is still within base directory
    if not str(full_path.resolve()).startswith(str(base_path)):
        raise ValueError("Path escapes base directory")

    # Check if file already exists
    if full_path.exists():
        raise ValueError(f"File already exists: {file_path}")

    # Validate content length
    if len(content) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"Content too large: {len(content)} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
        )

    # Ensure parent directory exists (within base directory)
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        raise RuntimeError(f"Failed to create parent directory: {e}")

    try:
        # Create and write to file
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"File created successfully: {file_path}"

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


DEFAULT_AGENT_TOOLS = [
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
