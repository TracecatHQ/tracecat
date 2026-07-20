"""Typed decoding for sandbox-produced result envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tracecat.logger import logger
from tracecat.sandbox.exceptions import SandboxFileSafetyError
from tracecat.sandbox.file_io import read_json_object_beneath
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult


class _ResultEnvelope(BaseModel):
    """Lenient result shape emitted by sandbox wrappers."""

    model_config = ConfigDict(extra="ignore")

    success: bool = Field(default=False)
    output: Any | None = Field(default=None)
    result: Any | None = Field(default=None)
    stdout: str | None = Field(default=None)
    stderr: str | None = Field(default=None)
    error: str | None = Field(default=None)
    error_code: SandboxErrorCode | None = Field(default=None)

    @field_validator("error_code", mode="before")
    @classmethod
    def empty_error_code_is_absent(cls, value: object) -> object:
        """Preserve the existing truthy-only error-code conversion."""
        return value if value else None


@dataclass(frozen=True)
class ResultEnvelopeOutcome:
    """Decoded result plus whether it came from a valid envelope."""

    result: SandboxResult
    valid_envelope: bool


def _invalid_result(
    *,
    error: str,
    stdout: str,
    stderr: str,
    stderr_limit: int,
    exit_code: int | None,
    execution_time_ms: float,
) -> ResultEnvelopeOutcome:
    return ResultEnvelopeOutcome(
        result=SandboxResult(
            success=False,
            error=error,
            stdout=stdout,
            stderr=stderr[:stderr_limit],
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
        ),
        valid_envelope=False,
    )


def decode_result_envelope(
    job_dir: Path,
    *,
    output_key: Literal["output", "result"],
    stdout: str,
    stderr: str,
    stderr_limit: int,
    invalid_result_error: str,
    log_label: Literal["sandbox", "action", "PID executor"],
    exit_code: int | None,
    execution_time_ms: float,
    max_bytes: int,
    stream_source: Literal["envelope", "process"],
    include_error_code: bool = False,
) -> ResultEnvelopeOutcome | None:
    """Decode result.json into a typed outcome, or return None when absent."""
    try:
        result_data = read_json_object_beneath(
            job_dir,
            Path("result.json"),
            max_bytes=max_bytes,
        )
    except SandboxFileSafetyError as exc:
        logger.warning(
            f"Rejected unsafe {log_label} result file",
            error=str(exc),
        )
        return _invalid_result(
            error=invalid_result_error,
            stdout=stdout,
            stderr=stderr,
            stderr_limit=stderr_limit,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
        )

    if result_data is None:
        return None

    try:
        envelope = _ResultEnvelope.model_validate(result_data)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            f"Rejected invalid {log_label} result fields",
            error=str(exc),
        )
        return _invalid_result(
            error=invalid_result_error,
            stdout=stdout,
            stderr=stderr,
            stderr_limit=stderr_limit,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
        )

    result_stdout: str = stdout
    result_stderr: str = stderr
    if stream_source == "envelope":
        if "stdout" in envelope.model_fields_set:
            result_stdout = cast(str, envelope.stdout)
        if "stderr" in envelope.model_fields_set:
            result_stderr = cast(str, envelope.stderr)

    return ResultEnvelopeOutcome(
        result=SandboxResult(
            success=envelope.success,
            output=getattr(envelope, output_key),
            stdout=result_stdout,
            stderr=result_stderr,
            error=envelope.error,
            error_code=envelope.error_code if include_error_code else None,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
        ),
        valid_envelope=True,
    )
