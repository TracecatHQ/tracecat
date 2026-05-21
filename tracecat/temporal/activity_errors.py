from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from temporalio.exceptions import ApplicationError

from tracecat.runtime.errors import RuntimeErrorOrigin, RuntimeErrorPhase
from tracecat.temporal.errors import ActivityRuntimeError


def _error_type(root: BaseException | None, error_type: str | None) -> str:
    if error_type is not None:
        return error_type
    if root is not None:
        return root.__class__.__name__
    return "RuntimeError"


def user(
    *,
    origin: RuntimeErrorOrigin,
    code: str,
    message: str,
    phase: RuntimeErrorPhase,
    root: BaseException | None = None,
    error_type: str | None = None,
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
    return ActivityRuntimeError.user(
        code=code,
        message=message,
        origin=origin,
        phase=phase,
        error_type=_error_type(root, error_type),
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


def platform(
    *,
    origin: RuntimeErrorOrigin,
    code: str,
    message: str,
    phase: RuntimeErrorPhase,
    root: BaseException | None = None,
    error_type: str | None = None,
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
    return ActivityRuntimeError.platform(
        code=code,
        message=message,
        origin=origin,
        phase=phase,
        error_type=_error_type(root, error_type),
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


def platform_or_infra(
    *,
    origin: RuntimeErrorOrigin,
    code: str,
    message: str,
    phase: RuntimeErrorPhase,
    root: BaseException,
    error_type: str | None = None,
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
    return ActivityRuntimeError.platform_or_infra(
        code=code,
        message=message,
        origin=origin,
        phase=phase,
        error_type=_error_type(root, error_type),
        root=root,
        details=details,
        action_ref=action_ref,
        stream_id=stream_id,
        workflow_exec_id=workflow_exec_id,
        affects_workflow=affects_workflow,
        metadata=metadata,
        infra_retryable=infra_retryable,
        infra_non_retryable=infra_non_retryable,
        platform_retryable=platform_retryable,
        platform_non_retryable=platform_non_retryable,
        ref=ref,
    )


@contextmanager
def platform_or_infra_boundary(
    *,
    origin: RuntimeErrorOrigin,
    code: str,
    message: str,
    phase: RuntimeErrorPhase,
    action_ref: str | None = None,
    stream_id: object | None = None,
    workflow_exec_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ref: str | None = None,
) -> Iterator[None]:
    try:
        yield
    except ApplicationError:
        raise
    except Exception as e:
        raise platform_or_infra(
            origin=origin,
            code=code,
            message=message,
            phase=phase,
            root=e,
            action_ref=action_ref,
            stream_id=stream_id,
            workflow_exec_id=workflow_exec_id,
            metadata=metadata,
            ref=ref,
        ) from e
