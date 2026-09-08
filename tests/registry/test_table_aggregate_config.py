"""Exercise deployment overrides before Pydantic and FastAPI cache their schemas."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(("default", "maximum"), [(2, 50), (1200, 2000)])
def test_server_limit_overrides(default: int, maximum: int) -> None:
    # A fresh interpreter loads the real configured request schema and gateway.
    # The child runs only the contract test, so this cannot recursively spawn.
    # tests.database gives each process its own randomly named database.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/registry/test_table_characterization.py::TestAggregateRows::test_omitted_limit_uses_server_default",
            "-o",
            "addopts=",
            "-n",
            "0",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "TRACECAT__LIMIT_AGG_GROUPS_DEFAULT": str(default),
            "TRACECAT__LIMIT_AGG_GROUPS_MAX": str(maximum),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
