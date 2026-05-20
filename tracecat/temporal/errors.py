from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
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

type RuntimeErrorFactory = (
    type[RuntimeUserError] | type[RuntimePlatformError] | type[RuntimeInfraError]
)


class TemporalErrorDetails(BaseModel):
    """Single V2 payload for Tracecat-owned Temporal ApplicationError details."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    kind: Literal["tracecat.temporal_error_details.v1"] = (
        "tracecat.temporal_error_details.v1"
    )
    payloads: tuple[Any, ...] = ()
    runtime_errors: dict[str, RuntimeErrorEnvelope] = Field(default_factory=dict)

    @property
    def first_payload(self) -> Any | None:
        return self.payloads[0] if self.payloads else None

    @classmethod
    def build(
        cls,
        *,
        payloads: Sequence[Any] = (),
        runtime_errors: Mapping[str, RuntimeErrorEnvelope] | None = None,
    ) -> TemporalErrorDetails:
        return cls(
            payloads=tuple(payloads),
            runtime_errors=dict(runtime_errors or {}),
        )

    @classmethod
    def with_runtime_error(
        cls,
        ref: str,
        envelope: RuntimeErrorEnvelope,
        *,
        payloads: Sequence[Any] = (),
    ) -> TemporalErrorDetails:
        return cls.build(payloads=payloads, runtime_errors={ref: envelope})

    @classmethod
    def from_application_error(cls, error: ApplicationError) -> TemporalErrorDetails:
        return cls.from_details(error.details)

    @classmethod
    def from_details(cls, details: Sequence[Any]) -> TemporalErrorDetails:
        if len(details) == 1 and (parsed := cls._from_payload(details[0])):
            return parsed
        return cls.build(payloads=details)

    def runtime_error(self, *, ref: str | None = None) -> RuntimeErrorEnvelope | None:
        if ref is not None and ref in self.runtime_errors:
            return self.runtime_errors[ref]
        return next(iter(self.runtime_errors.values()), None)

    @classmethod
    def runtime_error_from_details(
        cls, details: Sequence[Any], *, ref: str | None = None
    ) -> RuntimeErrorEnvelope | None:
        parsed = cls.from_details(details)
        return parsed.runtime_error(ref=ref)

    @classmethod
    def _from_payload(cls, payload: Any) -> TemporalErrorDetails | None:
        match payload:
            case TemporalErrorDetails() as details:
                return details
            case dict():
                try:
                    return cls.model_validate(payload)
                except Exception:
                    return None
        return None


class TemporalRuntimeError:
    """Build Temporal failures from first-class runtime errors."""

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
            TemporalErrorDetails.with_runtime_error(
                cls._runtime_error_ref(runtime_error, ref=ref),
                runtime_error.envelope,
                payloads=details,
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
        affects_workflow: bool | None = None,
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
                affects_workflow=affects_workflow,
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
        affects_workflow: bool | None = None,
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
            affects_workflow=affects_workflow,
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
        affects_workflow: bool | None = None,
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
            affects_workflow=affects_workflow,
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
        affects_workflow: bool | None = None,
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
            affects_workflow=affects_workflow,
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
        affects_workflow: bool | None = None,
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
                affects_workflow=affects_workflow,
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
            affects_workflow=affects_workflow,
            metadata=metadata,
            retryable=platform_retryable,
            non_retryable=platform_non_retryable,
            ref=ref,
        )


class ActivityRuntimeError(TemporalRuntimeError):
    """Build Temporal activity failures from first-class runtime errors."""


class WorkflowRuntimeError(TemporalRuntimeError):
    """Build Temporal workflow failures from first-class runtime errors."""


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
                if envelope := TemporalErrorDetails.runtime_error_from_details(
                    details, ref=ref
                ):
                    return envelope
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        match nested:
            case BaseException() as nested_error:
                current = nested_error
            case _:
                current = None
    return None
