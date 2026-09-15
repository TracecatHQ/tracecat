"""Typed decoding for sandbox-produced result envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    # Streams must be strings when present; an explicit null is malformed and
    # takes the invalid-result path rather than leaking None into SandboxResult.
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    # Action failures carry an ExecutorActionErrorInfo-shaped JSON object that
    # must pass through opaquely for the consumer to validate (action_runner).
    error: str | dict[str, Any] | None = Field(default=None)
    error_code: SandboxErrorCode | None = Field(default=None)

    @field_validator("error_code", mode="before")
    @classmethod
    def sanitize_untrusted_error_code(cls, value: object) -> object:
        """Sanitize an untrusted error code at the sandbox trust boundary.

        The envelope is written by jailed code, so a claimed error code must
        never escalate to INFRASTRUCTURE_FAILURE and an unknown value must
        degrade to WORKLOAD_FAILURE instead of failing envelope validation.
        """
        if value is None:
            return None
        if not isinstance(value, str):
            return SandboxErrorCode.WORKLOAD_FAILURE
        try:
            error_code = SandboxErrorCode(value)
        except ValueError:
            return SandboxErrorCode.WORKLOAD_FAILURE
        if error_code is SandboxErrorCode.INFRASTRUCTURE_FAILURE:
            return SandboxErrorCode.WORKLOAD_FAILURE
        return error_code


@dataclass(frozen=True, slots=True)
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
    include_error_code: bool = False,
) -> ResultEnvelopeOutcome:
    return ResultEnvelopeOutcome(
        result=SandboxResult(
            success=False,
            error=error,
            # A rejected envelope was produced by sandbox-controlled code, so
            # classify it as a workload failure: without an explicit code the
            # error policy treats the failure as retryable, letting a corrupt
            # result file trigger retry loops.
            error_code=(
                SandboxErrorCode.WORKLOAD_FAILURE if include_error_code else None
            ),
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
        # Log the exception class only: str(exc) can embed sandbox-controlled
        # paths from the rejected file.
        logger.warning(
            f"Rejected unsafe {log_label} result file",
            error=type(exc).__name__,
        )
        return _invalid_result(
            error=invalid_result_error,
            stdout=stdout,
            stderr=stderr,
            stderr_limit=stderr_limit,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
            include_error_code=include_error_code,
        )

    if result_data is None:
        return None

    try:
        envelope = _ResultEnvelope.model_validate(result_data)
    except (ValidationError, ValueError) as exc:
        # Log sanitized metadata only: rendering the full ValidationError would
        # copy the rejected input_value — sandbox-controlled, potentially
        # sensitive data — into executor logs.
        errors = (
            [
                f"{'.'.join(str(part) for part in error.get('loc', []))}: {error.get('type', 'unknown_error')}"
                for error in exc.errors()
            ]
            if isinstance(exc, ValidationError)
            else [type(exc).__name__]
        )
        logger.warning(
            f"Rejected invalid {log_label} result fields",
            errors=errors,
        )
        return _invalid_result(
            error=invalid_result_error,
            stdout=stdout,
            stderr=stderr,
            stderr_limit=stderr_limit,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
            include_error_code=include_error_code,
        )

    result_stdout: str = stdout
    result_stderr: str = stderr
    if stream_source == "envelope":
        if "stdout" in envelope.model_fields_set:
            result_stdout = envelope.stdout
        if "stderr" in envelope.model_fields_set:
            result_stderr = envelope.stderr

    return ResultEnvelopeOutcome(
        result=SandboxResult(
            success=envelope.success,
            # Explicit branch instead of getattr(): output_key is a Literal
            # of exactly two envelope fields, and direct attribute access lets
            # the type checker verify both branches (backend getattr rule).
            output=(envelope.output if output_key == "output" else envelope.result),
            stdout=result_stdout,
            stderr=result_stderr,
            error=envelope.error,
            error_code=envelope.error_code if include_error_code else None,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms,
        ),
        valid_envelope=True,
    )
