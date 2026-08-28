"""Tests for typed sandbox result-envelope decoding."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import orjson
import pytest
import pytest_mock

from tracecat.sandbox import result_envelope as result_envelope_module
from tracecat.sandbox.result_envelope import (
    ResultEnvelopeOutcome,
    decode_result_envelope,
)
from tracecat.sandbox.types import SandboxResult


def _decode(
    job_dir: Path,
    *,
    output_key: Literal["output", "result"] = "output",
    max_bytes: int = 1024,
    stream_source: Literal["envelope", "process"] = "envelope",
) -> ResultEnvelopeOutcome | None:
    return decode_result_envelope(
        job_dir,
        output_key=output_key,
        stdout="process stdout",
        stderr="process stderr",
        stderr_limit=7,
        invalid_result_error="invalid result",
        log_label="sandbox",
        exit_code=9,
        execution_time_ms=12.5,
        max_bytes=max_bytes,
        stream_source=stream_source,
        include_error_code=True,
    )


def _assert_invalid_result(outcome: ResultEnvelopeOutcome | None) -> None:
    assert outcome is not None
    assert outcome.valid_envelope is False
    assert outcome.result == SandboxResult(
        success=False,
        error="invalid result",
        stdout="process stdout",
        stderr="process",
        exit_code=9,
        execution_time_ms=12.5,
    )


def test_decode_result_envelope_returns_none_when_file_missing(
    tmp_path: Path,
) -> None:
    assert _decode(tmp_path) is None


@pytest.mark.parametrize(
    "result_bytes",
    [
        b"not json",
        b"\xff",
        orjson.dumps([{"success": True}]),
    ],
)
def test_decode_result_envelope_rejects_invalid_json_shapes(
    tmp_path: Path,
    result_bytes: bytes,
) -> None:
    (tmp_path / "result.json").write_bytes(result_bytes)

    _assert_invalid_result(_decode(tmp_path))


def test_decode_result_envelope_does_not_log_rejected_input_value(
    tmp_path: Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Validation warnings must not copy sandbox-controlled values into logs."""
    secret_marker = "customer-controlled-secret-token-12345"
    (tmp_path / "result.json").write_bytes(orjson.dumps({"success": [secret_marker]}))

    warning = mocker.patch.object(result_envelope_module.logger, "warning")

    _assert_invalid_result(_decode(tmp_path))

    warning.assert_called_once()
    assert secret_marker not in str(warning.call_args)
    assert secret_marker not in str(warning.call_args.kwargs)


def test_decode_result_envelope_rejects_oversized_file(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_bytes(orjson.dumps({"success": True}))

    _assert_invalid_result(_decode(tmp_path, max_bytes=4))


def test_decode_result_envelope_rejects_symlinked_result(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-result-envelope.json"
    outside.write_bytes(orjson.dumps({"success": True}))
    (tmp_path / "result.json").symlink_to(outside)

    _assert_invalid_result(_decode(tmp_path))


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("NaN", math.nan),
        ("Infinity", math.inf),
        ("-Infinity", -math.inf),
    ],
)
def test_decode_result_envelope_preserves_non_finite_numbers(
    tmp_path: Path,
    token: str,
    expected: float,
) -> None:
    """Decoder stays compatible with the stdlib JSON emitted by wrappers."""
    (tmp_path / "result.json").write_text(f'{{"success": true, "output": {token}}}')

    outcome = _decode(tmp_path)

    assert outcome is not None
    assert outcome.valid_envelope is True
    assert isinstance(outcome.result.output, float)
    if math.isnan(expected):
        assert math.isnan(outcome.result.output)
    else:
        assert outcome.result.output == expected


@pytest.mark.parametrize("output_key", ["output", "result"])
@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"success": [True]},
        {"error": 42},
    ],
)
def test_decode_result_envelope_rejects_wrong_fields_uniformly(
    tmp_path: Path,
    output_key: Literal["output", "result"],
    invalid_fields: object,
) -> None:
    (tmp_path / "result.json").write_bytes(orjson.dumps(invalid_fields))

    _assert_invalid_result(_decode(tmp_path, output_key=output_key))


@pytest.mark.parametrize(
    ("output_key", "expected_output"),
    [
        ("output", {"source": "output"}),
        ("result", {"source": "result"}),
    ],
)
def test_decode_result_envelope_selects_output_key_and_lenient_defaults(
    tmp_path: Path,
    output_key: Literal["output", "result"],
    expected_output: object,
) -> None:
    (tmp_path / "result.json").write_bytes(
        orjson.dumps(
            {
                output_key: expected_output,
                "stdout": "envelope stdout",
                "stderr": "envelope stderr",
            }
        )
    )

    result = _decode(tmp_path, output_key=output_key)

    assert result is not None
    assert result.valid_envelope is True
    assert result.result == SandboxResult(
        success=False,
        output=expected_output,
        stdout="envelope stdout",
        stderr="envelope stderr",
        exit_code=9,
        execution_time_ms=12.5,
    )


def test_decode_result_envelope_passes_structured_action_errors_through(
    tmp_path: Path,
) -> None:
    """Action failures carry ExecutorActionErrorInfo-shaped dicts in `error`."""
    structured_error = {
        "type": "ValueError",
        "message": "boom",
        "action_name": "tools.example.action",
        "filename": "<sandbox>",
        "function": "run",
        "lineno": 7,
    }
    (tmp_path / "result.json").write_bytes(
        orjson.dumps({"success": False, "result": None, "error": structured_error})
    )

    outcome = _decode(tmp_path, output_key="result", stream_source="process")

    assert outcome is not None
    assert outcome.valid_envelope is True
    assert outcome.result.error == structured_error


@pytest.mark.parametrize("stream_field", ["stdout", "stderr"])
def test_decode_result_envelope_rejects_null_streams(
    tmp_path: Path,
    stream_field: str,
) -> None:
    """Explicit null streams are malformed; SandboxResult streams are str."""
    (tmp_path / "result.json").write_bytes(
        orjson.dumps({"success": True, stream_field: None})
    )

    _assert_invalid_result(_decode(tmp_path))


def test_decode_result_envelope_can_preserve_process_streams(tmp_path: Path) -> None:
    """Action envelopes should not override captured process stdout or stderr."""
    (tmp_path / "result.json").write_bytes(
        orjson.dumps(
            {
                "success": True,
                "result": "done",
                "stdout": "envelope stdout",
                "stderr": "envelope stderr",
            }
        )
    )

    outcome = _decode(
        tmp_path,
        output_key="result",
        stream_source="process",
    )

    assert outcome is not None
    assert outcome.result.stdout == "process stdout"
    assert outcome.result.stderr == "process stderr"
