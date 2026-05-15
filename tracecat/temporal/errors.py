from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from temporalio.exceptions import ApplicationError

from tracecat.runtime.errors import (
    RuntimeErrorEnvelope,
    RuntimeErrorOrigin,
    RuntimeErrorPhase,
    RuntimeInfraError,
    RuntimePlatformError,
    RuntimeUserError,
    TracecatRuntimeError,
    is_known_infra_exception,
)

RUNTIME_ERROR_DETAILS_KEY = "runtime_errors"
type RuntimeErrorFactory = (
    type[RuntimeUserError] | type[RuntimePlatformError] | type[RuntimeInfraError]
)


def runtime_error_detail(
    ref: str, envelope: RuntimeErrorEnvelope
) -> dict[str, dict[str, RuntimeErrorEnvelope]]:
    return {RUNTIME_ERROR_DETAILS_KEY: {ref: envelope}}


class ActivityRuntimeError:
    """Build Temporal activity failures from first-class runtime errors."""

    @classmethod
    def _runtime_error_ref(
        cls,
        runtime_error: TracecatRuntimeError,
        *,
        ref: str | None = None,
    ) -> str:
        return (
            ref
            or runtime_error.envelope.action_ref
            or runtime_error.envelope.workflow_exec_id
            or runtime_error.envelope.code
        )

    @classmethod
    def _application_error(
        cls,
        runtime_error: TracecatRuntimeError,
        *,
        error_type: str,
        details: Sequence[Any] = (),
        non_retryable: bool,
        ref: str | None = None,
    ) -> ApplicationError:
        return ApplicationError(
            runtime_error.envelope.message,
            *details,
            runtime_error_detail(
                cls._runtime_error_ref(runtime_error, ref=ref),
                runtime_error.envelope,
            ),
            type=error_type,
            non_retryable=non_retryable,
        )

    @classmethod
    def _classified_error(
        cls,
        runtime_error_cls: RuntimeErrorFactory,
        *,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        error_type: str,
        root: BaseException | None = None,
        details: Sequence[Any] = (),
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        retryable: bool = False,
        non_retryable: bool = True,
        ref: str | None = None,
    ) -> ApplicationError:
        return cls._application_error(
            runtime_error_cls(
                code=code,
                message=message,
                origin=origin,
                phase=phase,
                retryable=retryable,
                root=root,
                action_ref=action_ref,
                stream_id=stream_id,
                workflow_exec_id=workflow_exec_id,
                metadata=metadata,
            ),
            error_type=error_type,
            details=details,
            non_retryable=non_retryable,
            ref=ref,
        )

    @classmethod
    def user(
        cls,
        *,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        error_type: str,
        root: BaseException | None = None,
        details: Sequence[Any] = (),
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        retryable: bool = False,
        non_retryable: bool = True,
        ref: str | None = None,
    ) -> ApplicationError:
        return cls._classified_error(
            RuntimeUserError,
            code=code,
            message=message,
            origin=origin,
            phase=phase,
            error_type=error_type,
            root=root,
            details=details,
            action_ref=action_ref,
            stream_id=stream_id,
            workflow_exec_id=workflow_exec_id,
            metadata=metadata,
            retryable=retryable,
            non_retryable=non_retryable,
            ref=ref,
        )

    @classmethod
    def platform(
        cls,
        *,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        error_type: str,
        root: BaseException | None = None,
        details: Sequence[Any] = (),
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        retryable: bool = False,
        non_retryable: bool = True,
        ref: str | None = None,
    ) -> ApplicationError:
        return cls._classified_error(
            RuntimePlatformError,
            code=code,
            message=message,
            origin=origin,
            phase=phase,
            error_type=error_type,
            root=root,
            details=details,
            action_ref=action_ref,
            stream_id=stream_id,
            workflow_exec_id=workflow_exec_id,
            metadata=metadata,
            retryable=retryable,
            non_retryable=non_retryable,
            ref=ref,
        )

    @classmethod
    def infra(
        cls,
        *,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        error_type: str,
        root: BaseException,
        details: Sequence[Any] = (),
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        retryable: bool = True,
        non_retryable: bool = False,
        ref: str | None = None,
    ) -> ApplicationError:
        return cls._classified_error(
            RuntimeInfraError,
            code=code,
            message=message,
            origin=origin,
            phase=phase,
            error_type=error_type,
            root=root,
            details=details,
            action_ref=action_ref,
            stream_id=stream_id,
            workflow_exec_id=workflow_exec_id,
            metadata=metadata,
            retryable=retryable,
            non_retryable=non_retryable,
            ref=ref,
        )

    @classmethod
    def platform_or_infra(
        cls,
        *,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        error_type: str,
        root: BaseException,
        details: Sequence[Any] = (),
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        infra_retryable: bool = True,
        infra_non_retryable: bool = False,
        platform_retryable: bool = False,
        platform_non_retryable: bool = True,
        ref: str | None = None,
    ) -> ApplicationError:
        if is_known_infra_exception(root):
            return cls.infra(
                code=code,
                message=message,
                origin=origin,
                phase=phase,
                error_type=error_type,
                root=root,
                details=details,
                action_ref=action_ref,
                stream_id=stream_id,
                workflow_exec_id=workflow_exec_id,
                metadata=metadata,
                retryable=infra_retryable,
                non_retryable=infra_non_retryable,
                ref=ref,
            )
        return cls.platform(
            code=code,
            message=message,
            origin=origin,
            phase=phase,
            error_type=error_type,
            root=root,
            details=details,
            action_ref=action_ref,
            stream_id=stream_id,
            workflow_exec_id=workflow_exec_id,
            metadata=metadata,
            retryable=platform_retryable,
            non_retryable=platform_non_retryable,
            ref=ref,
        )


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
