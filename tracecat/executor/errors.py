from __future__ import annotations

from typing import TYPE_CHECKING

from temporalio.exceptions import ApplicationError

from tracecat.dsl.types import ActionErrorInfo
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
from tracecat.temporal.errors import runtime_error_detail

if TYPE_CHECKING:
    from tracecat.dsl.schemas import StreamID


class ActionRuntimeError:
    @classmethod
    def _application_error(
        cls,
        runtime_error: TracecatRuntimeError,
        *,
        ref: str,
        stream_id: StreamID,
        attempt: int,
        error_type: str,
        operation: str = "execute_action",
        non_retryable: bool = True,
    ) -> ApplicationError:
        err_info = ActionErrorInfo(
            ref=ref,
            message=runtime_error.envelope.message,
            type=error_type,
            attempt=attempt,
            stream_id=stream_id,
        )
        return ApplicationError(
            err_info.format(operation),
            err_info,
            runtime_error_detail(ref, runtime_error.envelope),
            type=error_type,
            non_retryable=non_retryable,
        )

    @classmethod
    def user(
        cls,
        *,
        ref: str,
        stream_id: StreamID,
        attempt: int,
        code: str,
        message: str,
        error_type: str,
        phase: RuntimeErrorPhase,
        root: BaseException,
        operation: str = "execute_action",
        retryable: bool = False,
        non_retryable: bool = True,
    ) -> ApplicationError:
        return cls._application_error(
            RuntimeUserError(
                code=code,
                message=message,
                origin=RuntimeErrorOrigin.EXECUTOR,
                phase=phase,
                retryable=retryable,
                root=root,
                action_ref=ref,
                stream_id=stream_id,
            ),
            ref=ref,
            stream_id=stream_id,
            attempt=attempt,
            error_type=error_type,
            operation=operation,
            non_retryable=non_retryable,
        )

    @classmethod
    def platform(
        cls,
        *,
        ref: str,
        stream_id: StreamID,
        attempt: int,
        code: str,
        message: str,
        error_type: str,
        phase: RuntimeErrorPhase,
        root: BaseException,
        operation: str = "execute_action",
        retryable: bool = False,
        non_retryable: bool = True,
    ) -> ApplicationError:
        return cls._application_error(
            RuntimePlatformError(
                code=code,
                message=message,
                origin=RuntimeErrorOrigin.EXECUTOR,
                phase=phase,
                retryable=retryable,
                root=root,
                action_ref=ref,
                stream_id=stream_id,
            ),
            ref=ref,
            stream_id=stream_id,
            attempt=attempt,
            error_type=error_type,
            operation=operation,
            non_retryable=non_retryable,
        )

    @classmethod
    def infra(
        cls,
        *,
        ref: str,
        stream_id: StreamID,
        attempt: int,
        code: str,
        message: str,
        error_type: str,
        phase: RuntimeErrorPhase,
        root: BaseException,
        operation: str = "execute_action",
        retryable: bool = False,
        non_retryable: bool = True,
    ) -> ApplicationError:
        return cls._application_error(
            RuntimeInfraError(
                code=code,
                message=message,
                origin=RuntimeErrorOrigin.EXECUTOR,
                phase=phase,
                retryable=retryable,
                root=root,
                action_ref=ref,
                stream_id=stream_id,
            ),
            ref=ref,
            stream_id=stream_id,
            attempt=attempt,
            error_type=error_type,
            operation=operation,
            non_retryable=non_retryable,
        )

    @classmethod
    def platform_or_infra(
        cls,
        *,
        ref: str,
        stream_id: StreamID,
        attempt: int,
        code: str,
        message: str,
        error_type: str,
        phase: RuntimeErrorPhase,
        root: BaseException,
        operation: str = "execute_action",
        infra_retryable: bool = False,
        infra_non_retryable: bool = True,
        platform_retryable: bool = False,
        platform_non_retryable: bool = True,
    ) -> ApplicationError:
        if is_known_infra_exception(root):
            return cls.infra(
                ref=ref,
                stream_id=stream_id,
                attempt=attempt,
                code=code,
                message=message,
                error_type=error_type,
                phase=phase,
                root=root,
                operation=operation,
                retryable=infra_retryable,
                non_retryable=infra_non_retryable,
            )
        return cls.platform(
            ref=ref,
            stream_id=stream_id,
            attempt=attempt,
            code=code,
            message=message,
            error_type=error_type,
            phase=phase,
            root=root,
            operation=operation,
            retryable=platform_retryable,
            non_retryable=platform_non_retryable,
        )

    @classmethod
    def existing(
        cls,
        runtime_error: RuntimeErrorEnvelope | TracecatRuntimeError,
        *,
        ref: str,
        stream_id: StreamID,
        attempt: int,
        error_type: str,
        operation: str = "execute_action",
        non_retryable: bool,
    ) -> ApplicationError:
        match runtime_error:
            case RuntimeErrorEnvelope() as envelope:
                runtime_error = TracecatRuntimeError(envelope)
            case TracecatRuntimeError():
                pass
        return cls._application_error(
            runtime_error,
            ref=ref,
            stream_id=stream_id,
            attempt=attempt,
            error_type=error_type,
            operation=operation,
            non_retryable=non_retryable,
        )
