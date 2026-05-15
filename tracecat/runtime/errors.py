from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class RuntimeErrorKind(StrEnum):
    USER = "user"
    PLATFORM = "platform"
    INFRA = "infra"


class RuntimeErrorOrigin(StrEnum):
    DSL = "dsl"
    EXECUTOR = "executor"
    TEMPORAL = "temporal"
    UNKNOWN = "unknown"


class RuntimeErrorPhase(StrEnum):
    PREPARE = "prepare"
    USER_CODE = "user_code"
    COLLECT = "collect"
    ROUTE = "route"
    CLEANUP = "cleanup"
    UNKNOWN = "unknown"


class RuntimeErrorBinding(StrEnum):
    IN_BAND = "in_band"
    OUT_OF_BAND = "out_of_band"


class RuntimeErrorEnvelope(BaseModel):
    """Classifies runtime errors independently from user-facing error payloads."""

    kind: RuntimeErrorKind
    code: str
    message: str
    origin: RuntimeErrorOrigin
    phase: RuntimeErrorPhase
    binding: RuntimeErrorBinding = RuntimeErrorBinding.IN_BAND
    affects_workflow: bool = False
    retryable: bool = False
    root_type: str | None = None
    root_message: str | None = None
    action_ref: str | None = None
    stream_id: str | None = None
    workflow_exec_id: str | None = None
    captured: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        kind: RuntimeErrorKind,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        binding: RuntimeErrorBinding = RuntimeErrorBinding.IN_BAND,
        affects_workflow: bool = False,
        retryable: bool = False,
        root: BaseException | None = None,
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeErrorEnvelope:
        return cls(
            kind=kind,
            code=code,
            message=message,
            origin=origin,
            phase=phase,
            binding=binding,
            affects_workflow=affects_workflow,
            retryable=retryable,
            root_type=root.__class__.__name__ if root is not None else None,
            root_message=str(root) if root is not None else None,
            action_ref=action_ref,
            stream_id=str(stream_id) if stream_id is not None else None,
            workflow_exec_id=workflow_exec_id,
            metadata=metadata or {},
        )


class TracecatRuntimeError(Exception):
    """Runtime exception carrying a first-class error envelope."""

    def __init__(self, envelope: RuntimeErrorEnvelope) -> None:
        super().__init__(envelope.message)
        self.envelope = envelope


class _TypedRuntimeError(TracecatRuntimeError):
    """Runtime exception that builds an envelope from a fixed taxonomy kind."""

    kind: ClassVar[RuntimeErrorKind]
    affects_workflow_by_default: ClassVar[bool]

    def __init__(
        self,
        *,
        code: str,
        message: str,
        origin: RuntimeErrorOrigin,
        phase: RuntimeErrorPhase,
        binding: RuntimeErrorBinding = RuntimeErrorBinding.IN_BAND,
        affects_workflow: bool | None = None,
        retryable: bool = False,
        root: BaseException | None = None,
        action_ref: str | None = None,
        stream_id: object | None = None,
        workflow_exec_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            RuntimeErrorEnvelope.build(
                kind=self.kind,
                code=code,
                message=message,
                origin=origin,
                phase=phase,
                binding=binding,
                affects_workflow=(
                    self.affects_workflow_by_default
                    if affects_workflow is None
                    else affects_workflow
                ),
                retryable=retryable,
                root=root,
                action_ref=action_ref,
                stream_id=stream_id,
                workflow_exec_id=workflow_exec_id,
                metadata=metadata,
            )
        )


class RuntimeUserError(_TypedRuntimeError):
    """Runtime error caused by user-authored config, permissions, or code."""

    kind = RuntimeErrorKind.USER
    affects_workflow_by_default = False


class RuntimePlatformError(_TypedRuntimeError):
    """Runtime error caused by Tracecat platform logic."""

    kind = RuntimeErrorKind.PLATFORM
    affects_workflow_by_default = True


class RuntimeInfraError(_TypedRuntimeError):
    """Runtime error caused by infrastructure or backing services."""

    kind = RuntimeErrorKind.INFRA
    affects_workflow_by_default = True


def is_known_infra_exception(error: BaseException) -> bool:
    match error:
        case TimeoutError() | OSError() | ConnectionError():
            return True
        case _:
            module = error.__class__.__module__
            return module.startswith(("botocore", "boto3", "aiohttp", "httpx", "redis"))
