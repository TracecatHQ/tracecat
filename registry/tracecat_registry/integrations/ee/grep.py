"""Grep action for Tracecat.

This module provides S3-based grep functionality with built-in resource management
and concurrency limiting to prevent system overload.

Concurrency Limiting
-------------------
To prevent resource exhaustion, this module implements two levels of concurrency control:

1. **S3 Operations**: Limited by `TRACECAT__S3_CONCURRENCY_LIMIT` (default: 50)
   - Controls concurrent S3 API calls (get_object, put_object, etc.)
   - Prevents overwhelming S3 service and network resources
   - Higher limit since S3 operations are network I/O bound
   - Configured via environment variable `TRACECAT__S3_CONCURRENCY_LIMIT`

2. **File Operations**: Limited by `TRACECAT__FILE_CONCURRENCY_LIMIT` (default: 8x CPU count, capped at 64)
   - Controls concurrent file writes and ripgrep executions
   - Prevents disk I/O bottlenecks and excessive process spawning
   - Scales with CPU count since ripgrep is CPU-intensive
   - For 8vCPU instance: defaults to 64 concurrent operations
   - Configured via environment variable `TRACECAT__FILE_CONCURRENCY_LIMIT`

The concurrency limits use asyncio.Semaphore to ensure that only a specified number
of operations can run simultaneously, queuing additional requests until resources
become available.

Configuration
------------
Set these environment variables to adjust concurrency limits:

```bash
export TRACECAT__S3_CONCURRENCY_LIMIT=100     # Allow up to 100 concurrent S3 operations
export TRACECAT__FILE_CONCURRENCY_LIMIT=80    # Allow up to 80 concurrent file operations
```

**Rationale for Defaults:**
- **S3 limit (50)**: Network I/O operations can handle high concurrency without overwhelming local resources
- **File limit (8x CPU)**: Optimized for CPU-bound ripgrep operations, scales with available processing power
- **Cap at 64**: Prevents excessive resource usage even on high-CPU instances

**Hardware Scaling:**
- 4 vCPU: 32 concurrent file operations
- 8 vCPU: 64 concurrent file operations (capped)
- 16 vCPU: 64 concurrent file operations (capped)

Caching
-------
The module also implements intelligent caching using diskcache to reduce redundant
S3 operations and improve performance for repeated requests.
"""

import orjson
import tempfile
from pathlib import Path
from typing import Annotated, Any
from typing_extensions import Doc
import subprocess
import asyncio
from diskcache import FanoutCache

from tracecat import config
from tracecat_registry import registry
from tracecat_registry.integrations.amazon_s3 import s3_secret, get_objects

# Semaphore to limit concurrent file operations to prevent resource exhaustion
# This limits the number of concurrent file writes and ripgrep operations
_file_semaphore = asyncio.Semaphore(config.TRACECAT__FILE_CONCURRENCY_LIMIT)

S3_CACHE = FanoutCache(
    directory=Path.home() / ".cache" / "s3",
    timeout=3600,  # 1 hour TTL
    size_limit=1024**3,  # 1GB limit
)


def _ripgrep(
    pattern: str, dir_path: Path, max_columns: int | None = None
) -> str | list[str] | dict[str, Any] | list[dict[str, Any]]:
    # Validate and resolve dir_path to prevent directory traversal
    dir_path = dir_path.resolve()

    # Ensure the directory exists and is actually a directory
    if not dir_path.exists() or not dir_path.is_dir():
        raise ValueError(f"Invalid directory path: {dir_path}")

    # Build command with security-focused flags
    args = [
        "rg",
        "--json",
        "--no-follow",  # Don't follow symbolic links
        "--no-ignore",  # Don't respect .gitignore files
        "--no-config",  # Don't read configuration files
        "--max-filesize",
        str(config.TRACECAT__MAX_FILE_SIZE_BYTES),  # Limit file size to prevent DoS
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
            "PATH": config.TRACECAT__SYSTEM_PATH,  # Use configurable system PATH
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


async def _get_cached_objects(bucket: str, keys: list[str]) -> list[str]:
    """Get S3 objects with caching by bucket/key."""
    # Separate keys into cached and uncached
    cached_objects = {}
    uncached_keys = []

    for key in keys:
        cache_key = f"{bucket}/{key}"
        cached_content = S3_CACHE.get(cache_key)
        if cached_content is not None:
            cached_objects[key] = cached_content
        else:
            uncached_keys.append(key)

    # Fetch uncached objects in parallel with built-in concurrency limiting
    if uncached_keys:
        # The get_objects function already has concurrency limiting via semaphore
        new_objects = await get_objects(bucket, uncached_keys)

        # Cache the new objects
        for key, content in zip(uncached_keys, new_objects):
            cache_key = f"{bucket}/{key}"
            S3_CACHE.set(cache_key, content)
            cached_objects[key] = content

    # Return objects in the original key order
    return [cached_objects[key] for key in keys]


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
) -> str | list[str] | dict[str, Any] | list[dict[str, Any]]:
    # Validate inputs
    if not bucket or not keys:
        raise ValueError("Bucket and keys must be provided")

    if len(keys) > 1000:
        raise ValueError("Cannot process more than 1000 keys at once")

    # Get objects (with caching and built-in S3 concurrency limiting)
    objects = await _get_cached_objects(bucket, keys)

    # Create temporary directory and files with concurrency limiting
    async with _file_semaphore:
        with tempfile.TemporaryDirectory(prefix="tracecat_grep_") as temp_dir:
            temp_path = Path(temp_dir)

            # Write each object to a temporary file
            for key, content in zip(keys, objects):
                # Sanitize key to create valid filename
                # Remove any path separators and special characters
                safe_filename = "".join(
                    c if c.isalnum() or c in "._-" else "_" for c in key
                )
                # Ensure filename is not empty and doesn't start with a dot
                if not safe_filename or safe_filename.startswith("."):
                    safe_filename = f"file_{hash(key)}"

                file_path = temp_path / safe_filename
                file_path.write_text(content, encoding="utf-8")

            return _ripgrep(pattern, Path(temp_path), max_columns)
