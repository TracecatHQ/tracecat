"""Subprocess entrypoint for sandboxed action execution.

This module is invoked by nsjail to run registry actions in untrusted mode.
It does NOT import tracecat to minimize cold start time.

Input/Output:
- Reads from /work/input.json
- Writes to /work/result.json
"""

from __future__ import annotations

import sys
from pathlib import Path

import orjson

# Import minimal_runner functions directly - NO tracecat imports
from tracecat.executor.minimal_runner import main_minimal


def main() -> None:
    """Subprocess entrypoint for untrusted sandbox execution."""
    # Determine input source: file (sandbox) or stdin (direct)
    input_path = Path("/work/input.json")
    output_path = Path("/work/result.json")

    if input_path.exists():
        input_data = orjson.loads(input_path.read_bytes())
        use_file_io = True
    else:
        input_bytes = sys.stdin.buffer.read()
        input_data = orjson.loads(input_bytes)
        use_file_io = False

    # Run the action using minimal runner (no tracecat imports)
    result = main_minimal(input_data)

    # Output result
    result_bytes = orjson.dumps(result)

    if use_file_io:
        output_path.write_bytes(result_bytes)
    else:
        sys.stdout.buffer.write(result_bytes)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
