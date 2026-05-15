from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from temporalio.exceptions import ApplicationError

from tracecat.runtime.errors import RuntimeErrorEnvelope, TracecatRuntimeError

RUNTIME_ERROR_DETAILS_KEY = "runtime_errors"


def runtime_error_detail(
    ref: str, envelope: RuntimeErrorEnvelope
) -> dict[str, dict[str, RuntimeErrorEnvelope]]:
    return {RUNTIME_ERROR_DETAILS_KEY: {ref: envelope}}


def _validate_envelope(value: Any) -> RuntimeErrorEnvelope | None:
    match value:
        case RuntimeErrorEnvelope() as envelope:
            return envelope
        case dict():
            try:
                return RuntimeErrorEnvelope.model_validate(value)
            except Exception:
                return None
        case _:
            return None


def extract_runtime_error_from_details(
    details: Sequence[Any], *, ref: str | None = None
) -> RuntimeErrorEnvelope | None:
    for detail in details:
        match detail:
            case RuntimeErrorEnvelope() as envelope:
                return envelope
            case dict() as detail_map:
                match detail_map.get(RUNTIME_ERROR_DETAILS_KEY):
                    case dict() as runtime_errors:
                        if ref is not None and ref in runtime_errors:
                            return _validate_envelope(runtime_errors[ref])
                        for value in runtime_errors.values():
                            if envelope := _validate_envelope(value):
                                return envelope
                    case _:
                        if envelope := _validate_envelope(detail_map):
                            return envelope
            case _:
                continue
    return None


def extract_runtime_error(
    error: BaseException, *, ref: str | None = None
) -> RuntimeErrorEnvelope | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None:
        current_id = id(current)
        if current_id in seen:
            return None
        seen.add(current_id)
        match current:
            case TracecatRuntimeError(envelope=envelope):
                return envelope
            case ApplicationError(details=details) if details:
                if envelope := extract_runtime_error_from_details(details, ref=ref):
                    return envelope
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        match nested:
            case BaseException() as nested_error:
                current = nested_error
            case _:
                current = None
    return None
