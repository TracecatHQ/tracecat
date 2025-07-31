"""Grep action for Tracecat.

This module provides S3-based grep functionality with built-in resource management
and concurrency limiting to prevent system overload.

Concurrency Limiting
-------------------
To prevent resource exhaustion, this module implements S3 concurrency control:

**S3 Operations**: Limited by `TRACECAT__S3_CONCURRENCY_LIMIT` (default: 50)
- Controls concurrent S3 API calls (get_object, put_object, etc.)
- Prevents overwhelming S3 service and network resources
- Higher limit since S3 operations are network I/O bound
- Configured via environment variable `TRACECAT__S3_CONCURRENCY_LIMIT`

File operations (writing temp files, running ripgrep) are kept synchronous since they're
fast local operations that don't require concurrency limiting.

The S3 concurrency limits use asyncio.Semaphore to ensure that only a specified number
of S3 operations can run simultaneously, queuing additional requests until resources
become available.

Configuration
------------
Set this environment variable to adjust S3 concurrency limits:

```bash
export TRACECAT__S3_CONCURRENCY_LIMIT=100     # Allow up to 100 concurrent S3 operations
```

**Rationale:**
- **S3 limit (50)**: Network I/O operations can handle high concurrency without overwhelming local resources
- **File operations**: No limiting needed - local temp file operations are fast and don't require throttling

"""

import re
import jsonpath_ng.ext
import jsonpath_ng
import orjson
import tempfile
from pathlib import Path
from typing import Annotated, Any
from typing_extensions import Doc
import subprocess
import jsonpath_ng.exceptions

from tracecat.config import TRACECAT__MAX_FILE_SIZE_BYTES, TRACECAT__SYSTEM_PATH
from tracecat_registry import registry
from tracecat_registry.integrations.amazon_s3 import s3_secret, get_objects

# Similar to Cursor read_file tool
MAX_MATCHES = 250


def _validate_file_security(
    file_path: Path,
    *,
    pattern_or_expression: str,
    pattern_name: str = "pattern",
    max_pattern_length: int = 1000,
    require_file: bool = True,
) -> None:
    """Common security validation for file operations.

    Args:
        file_path: Path to validate
        pattern_or_expression: Pattern/expression to validate
        pattern_name: Name of the pattern for error messages
        max_pattern_length: Maximum allowed pattern length
        require_file: If True, path must be a file; if False, must be a directory

    Raises:
        ValueError: If validation fails
    """
    # Validate and resolve path to prevent directory traversal
    file_path = file_path.resolve()

    # Ensure the path exists and is the correct type
    if not file_path.exists():
        raise ValueError(f"Path does not exist: {file_path}")

    if require_file and not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")
    elif not require_file and not file_path.is_dir():
        raise ValueError(f"Path is not a directory: {file_path}")

    # Basic validation of pattern/expression
    if not pattern_or_expression or not pattern_or_expression.strip():
        raise ValueError(f"{pattern_name} cannot be empty")

    # Reasonable length limit to prevent DoS
    if len(pattern_or_expression) > max_pattern_length:
        raise ValueError(f"{pattern_name} too long (max {max_pattern_length} chars)")

    # Check for null bytes which could indicate binary injection
    if "\x00" in pattern_or_expression:
        raise ValueError(f"{pattern_name} contains null bytes")


def _validate_file_size(file_path: Path) -> None:
    """Validate file size to prevent DoS attacks.

    Args:
        file_path: Path to check

    Raises:
        ValueError: If file is too large
    """
    file_size = file_path.stat().st_size
    if file_size > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File too large: {file_size} bytes (max {TRACECAT__MAX_FILE_SIZE_BYTES})"
        )


def grep_search(
    pattern: str, dir_path: Path, max_columns: int | None = None
) -> str | list[str] | dict[str, Any] | list[dict[str, Any]]:
    """Search for a pattern in a directory using ripgrep. Returns max 250 matches.

    Args:
        pattern: Regex pattern to search for.
        dir_path: Directory to search in.
        max_columns: Maximum number of columns to grep.

    Returns:
        Matched text.

    References: https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md
    """
    # Use consolidated security validation
    _validate_file_security(
        dir_path,
        pattern_or_expression=pattern,
        pattern_name="regex pattern",
        require_file=False,  # ripgrep works on directories
    )

    # Fast-fail on obviously invalid regular expressions so the model can retry quickly
    re.compile(pattern)

    # Build command with security-focused flags
    args = [
        "rg",
        "--json",
        "--no-follow",  # Don't follow symbolic links
        "--no-ignore",  # Don't respect .gitignore files
        "--no-config",  # Don't read configuration files
        "--max-count",
        "250",  # Limit results to prevent memory exhaustion
        "--max-filesize",
        str(TRACECAT__MAX_FILE_SIZE_BYTES),  # Limit file size to prevent DoS
        "--threads",
        "4",  # Limit threads to prevent resource exhaustion
    ]

    if max_columns:
        args.extend(["--max-columns", str(max_columns)])

    # Add pattern and directory as separate arguments
    args.extend([pattern, dir_path.as_posix()])

    # Run with shell=False and restricted environment
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        shell=False,
        env={
            "PATH": TRACECAT__SYSTEM_PATH,  # Use configurable system PATH
            "HOME": dir_path.as_posix(),  # Set HOME to the temp directory
        },
        cwd=dir_path.as_posix(),  # Set working directory to the temp directory
    )

    if result.returncode not in (0, 1):  # ripgrep returns 1 when no matches found
        raise RuntimeError(
            f"ripgrep failed with code {result.returncode}: {result.stderr}"
        )

    # Parse ripgrep JSON output (one JSON object per line)
    if not result.stdout:
        return []

    # Optimized NDJSON parsing using list comprehension and splitlines()
    json_objects = []
    for line in result.stdout.splitlines():
        if line:  # Skip empty lines
            try:
                json_objects.append(orjson.loads(line))
            except orjson.JSONDecodeError:
                # Skip malformed lines
                continue

    return json_objects


def jsonpath_find(
    expression: str,
    file_path: Path,
) -> list[Any]:
    """Find matches in a JSON file using a JSONPath expression. Returns max 250 matches.

    Args:
        expression: JSONPath expression to search for.
        file_path: Path to the JSON file to search in.

    Returns:
        Matched values.

    Raises:
        ValueError: If file path is invalid or expression is malformed
        RuntimeError: If JSON parsing or JSONPath evaluation fails

    References: https://pypi.org/project/jsonpath-ng/
    """
    # Use consolidated security validation
    _validate_file_security(
        file_path,
        pattern_or_expression=expression,
        pattern_name="JSONPath expression",
        require_file=True,  # jsonpath_find works on files
    )

    try:
        # Check file size before reading to prevent DoS
        _validate_file_size(file_path)

        # Read file with proper encoding and error handling
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, IOError) as e:
            raise RuntimeError(f"File read error: {e}")

        # Parse JSON content. Let JSONDecodeError propagate so callers can differentiate.
        json_data = orjson.loads(content)

        # Parse and validate JSONPath expression. Allow JsonPathParserError to propagate.
        jsonpath_expr = jsonpath_ng.ext.parse(expression)

        # Execute JSONPath search
        matches = jsonpath_expr.find(json_data)
        match_values = [found.value for found in matches[:MAX_MATCHES]]

        return match_values

    except Exception as e:
        # Preserve specific parsing exceptions; wrap only truly unexpected errors
        if isinstance(
            e,
            (
                ValueError,
                jsonpath_ng.exceptions.JsonPathParserError,
                orjson.JSONDecodeError,
            ),
        ):
            raise
        raise RuntimeError(f"JSONPath evaluation failed: {e}")


def jsonpath_find_and_replace(
    expression: str,
    file_path: Path,
    replacement: str | int | float | bool | list[Any] | dict[str, Any] | None,
) -> str:
    """Find and replace all matches of a JSONPath expression in a file.

    Args:
        expression: JSONPath expression to search for.
        file_path: Path to the JSON file to modify.
        replacement: Replacement value for matched elements.

    Returns:
        The modified JSON content as a string.

    Raises:
        ValueError: If file path is invalid or expression is malformed
        RuntimeError: If JSON parsing, JSONPath evaluation, or file operations fail

    References: https://pypi.org/project/jsonpath-ng/
    """
    # Use consolidated security validation
    _validate_file_security(
        file_path,
        pattern_or_expression=expression,
        pattern_name="JSONPath expression",
        require_file=True,  # jsonpath_find_and_replace works on files
    )

    # Validate replacement value
    if replacement is None:
        raise ValueError("Replacement value cannot be None")

    try:
        # Check file size before reading to prevent DoS
        _validate_file_size(file_path)

        # Read file with proper encoding and error handling
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, IOError) as e:
            raise RuntimeError(f"File read error: {e}")

        # Parse JSON content. Allow JSONDecodeError to propagate.
        json_data = orjson.loads(content)

        # Parse and validate JSONPath expression. Preserve JsonPathParserError.
        jsonpath_expr = jsonpath_ng.ext.parse(expression)

        # Find matches and perform replacements
        try:
            # Find all matches
            matches = jsonpath_expr.find(json_data)

            # Perform replacements in reverse order to maintain correct indices
            # This is important when dealing with array indices that might shift
            for match in reversed(matches):
                match.full_path.update(json_data, replacement)

            # Convert back to JSON string with proper formatting
            modified_json = orjson.dumps(
                json_data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS
            ).decode("utf-8")

            # Write the modified content back to the file
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(modified_json)
            except (OSError, IOError) as e:
                raise RuntimeError(f"File write error: {e}")

            return modified_json

        except Exception as e:
            # Preserve specific parsing exceptions; wrap only unexpected ones
            if isinstance(
                e,
                (
                    ValueError,
                    jsonpath_ng.exceptions.JsonPathParserError,
                    orjson.JSONDecodeError,
                ),
            ):
                raise
            raise RuntimeError(f"JSONPath find and replace operation failed: {e}")

    except Exception as e:
        # Preserve specific parsing exceptions; wrap only truly unexpected errors
        if isinstance(
            e,
            (
                ValueError,
                jsonpath_ng.exceptions.JsonPathParserError,
                orjson.JSONDecodeError,
            ),
        ):
            raise
        raise RuntimeError(f"JSONPath find and replace operation failed: {e}")


@registry.register(
    default_title="Grep S3 objects",
    description="Download multiple S3 objects and grep them.",
    display_group="Grep",
    doc_url="https://www.gnu.org/software/grep/manual/grep.html",
    namespace="tools.grep",
    secrets=[s3_secret],
)
async def s3(
    bucket: Annotated[str, Doc("S3 bucket name.")],
    keys: Annotated[list[str], Doc("S3 object keys.")],
    pattern: Annotated[str, Doc("Regex pattern to grep.")],
    max_columns: Annotated[int, Doc("Maximum number of columns to grep.")] = 10000,
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS S3 service."),
    ] = None,
) -> str | list[str] | dict[str, Any] | list[dict[str, Any]]:
    # Validate inputs
    if not bucket or not keys:
        raise ValueError("Bucket and keys must be provided")

    if len(keys) > 1000:
        raise ValueError("Cannot process more than 1000 keys at once")

    # Get objects (with built-in S3 concurrency limiting)
    objects = await get_objects(bucket, keys, endpoint_url)

    # Create temporary directory and write files synchronously
    # File I/O is typically fast for temp directories, so no concurrency limiting needed
    with tempfile.TemporaryDirectory(prefix="tracecat_grep_") as temp_dir:
        temp_path = Path(temp_dir)

        # Write each object to a temporary file (synchronous - fast for local temp files)
        for key, content in zip(keys, objects):
            # Sanitize key to create valid filename
            safe_filename = "".join(
                c if c.isalnum() or c in "._-" else "_" for c in key
            )
            # Ensure filename is not empty and doesn't start with a dot
            if not safe_filename or safe_filename.startswith("."):
                safe_filename = f"file_{hash(key)}"

            file_path = temp_path / safe_filename
            file_path.write_text(content, encoding="utf-8")

        # Run ripgrep on all files (this is CPU-bound, not I/O limited)
        return grep_search(pattern, temp_path, max_columns)
