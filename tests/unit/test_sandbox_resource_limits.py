"""Exercise standalone runner failures and their host-side envelope decoding."""

import subprocess
import sys
from pathlib import Path
from typing import Literal

import orjson
import pytest

from tracecat.executor import minimal_runner
from tracecat.sandbox.result_envelope import decode_result_envelope
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult
from tracecat.sandbox.wrapper import WRAPPER_SCRIPT


def _run_runner(
    tmp_path: Path, runner: Literal["script", "action"], script: str
) -> SandboxResult:
    (tmp_path / "script.py").write_text(script)
    (tmp_path / "inputs.json").write_text("{}")
    (tmp_path / "input.json").write_bytes(
        orjson.dumps(
            {
                "resolved_context": {
                    "action_impl": {"type": "udf", "module": "script", "name": "main"},
                    "evaluated_args": {},
                },
                "secret_env": {},
            }
        )
    )
    source = (
        WRAPPER_SCRIPT
        if runner == "script"
        else Path(minimal_runner.__file__).read_text()
    )
    runner_path = tmp_path / "runner.py"
    runner_path.write_text(source.replace('"/work/', f'"{tmp_path}/'))
    completed = subprocess.run(
        [sys.executable, "-B", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    outcome = decode_result_envelope(
        tmp_path,
        output_key="output" if runner == "script" else "result",
        stdout=completed.stdout,
        stderr=completed.stderr,
        stderr_limit=4096,
        invalid_result_error="Invalid result",
        log_label="sandbox" if runner == "script" else "action",
        exit_code=completed.returncode,
        execution_time_ms=0,
        max_bytes=1024 * 1024,
        stream_source="envelope",
        include_error_code=True,
    )
    assert outcome is not None, completed.stderr
    assert outcome.valid_envelope, completed.stderr
    return outcome.result


@pytest.mark.parametrize("runner", ["script", "action"])
@pytest.mark.parametrize("target", ["user_file", "result_file"])
def test_file_size_limit_produces_resource_envelope(
    tmp_path: Path,
    runner: Literal["script", "action"],
    target: str,
) -> None:
    operation = (
        'with open("payload.bin", "wb") as output:\n        output.write(b"x" * 8192)'
        if target == "user_file"
        else 'return "x" * 8192'
    )
    result = _run_runner(
        tmp_path,
        runner,
        f"""import resource

def main():
    resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
    {operation}
""",
    )
    assert result.success is False
    assert result.error_code is SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED
    message = (
        result.error["message"] if isinstance(result.error, dict) else result.error
    )
    assert isinstance(message, str)
    assert "file size limit" in message
    assert "memory" not in message


@pytest.mark.parametrize("runner", ["script", "action"])
@pytest.mark.parametrize("error_name", ["ENOSPC", "EACCES", "EAGAIN"])
def test_other_oserrors_remain_ordinary_workload_errors(
    tmp_path: Path, runner: Literal["script", "action"], error_name: str
) -> None:
    result = _run_runner(
        tmp_path,
        runner,
        f"""import errno

def main():
    raise OSError(errno.{error_name}, "synthetic syscall failure")
""",
    )
    assert result.success is False
    assert result.error_code is None
