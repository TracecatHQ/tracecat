from __future__ import annotations

import functools
import inspect
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Concatenate, cast

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import (
    AuditAction,
    AuditMetadata,
    AuditResourceType,
)
from tracecat.auth.types import PlatformRole, Role
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.sanitization import redact_sensitive_text
from tracecat.service import BaseService


@dataclass(frozen=True)
class AuditEventDetails:
    """Audit fields returned by an audit metadata callback.

    Attributes:
        action: Replaces the decorator's default action when returned by
            ``attempt_metadata``. It is ignored when returned by
            ``terminal_metadata``.
        resource_id: Replaces the automatically extracted resource ID. A value
            from ``attempt_metadata`` applies to every event; a value from
            ``terminal_metadata``
            applies only to the terminal event.
        data: Metadata to store on the event. Data from ``attempt_metadata`` is
            used for both the attempt and terminal events. Non-``None`` data
            from ``terminal_metadata`` replaces it for the terminal event only.
            The audit service applies its metadata allowlist before delivery;
            callers must still avoid constructing metadata from raw content or
            secret values.
        status: Replaces the inferred terminal status when returned by
            ``terminal_metadata``. It is ignored when returned by
            ``attempt_metadata``.
        emit: When ``False`` from ``attempt_metadata``, executes the decorated
            function without emitting any audit events. It is ignored by
            ``terminal_metadata``.
    """

    action: AuditAction | None = None
    resource_id: uuid.UUID | None = None
    data: AuditMetadata | None = None
    status: AuditEventStatus | None = None
    emit: bool = True


type AuditDetailsResult = AuditEventDetails | Awaitable[AuditEventDetails]
type AuditAttemptMetadataFn[**P] = Callable[P, AuditDetailsResult]
type AuditTerminalMetadataFn[R, **P] = Callable[Concatenate[R, P], AuditDetailsResult]

# Mapping of resource types to their default resource_id_attr names
_RESOURCE_ID_ATTR_MAP: dict[str, str] = {
    "workflow": "workflow_id",
    "workflow_execution": "wf_id",
    "schedule": "schedule_id",
    "organization_member": "user_id",
    "organization_session": "session_id",
    "organization": "org_id",
    "organization_domain": "domain_id",
    "organization_invitation": "id",
    "organization_tier": "org_id",
    "platform_registry_repository": "repository_id",
    "platform_registry_version": "version_id",
    "tier": "tier_id",
    "user": "id",
}


def audit_log[**P, R](
    *,
    resource_type: AuditResourceType,
    action: AuditAction,
    resource_id_attr: str | None = None,
    attempt_metadata: AuditAttemptMetadataFn[P] | None = None,
    terminal_metadata: AuditTerminalMetadataFn[R, P] | None = None,
) -> Callable[
    [Callable[P, Awaitable[R]]],
    Callable[P, Awaitable[R]],
]:
    """Audit an asynchronous operation from its attempt through its terminal event.

    The wrapper emits an ``ATTEMPT`` event before calling the function, followed
    by a ``SUCCESS`` or ``FAILURE`` event. A returned object with
    ``success=False`` or ``ok=False`` is treated as a failure even when the call
    does not raise. If the call raises, the failure event uses a fresh database
    session so it is not lost with the operation's aborted transaction, and the
    original exception is re-raised.

    The actor role is resolved from the service instance or a bound ``role``
    argument, then from the role context. Auditing is skipped when there is no
    auditable actor. Internal service roles that carry the initiating user's ID
    are audited and attributed to that user; unattributed service traffic has
    no actor and is skipped. Event creation and
    detail callbacks are best-effort: their failures are logged without changing
    the result of the decorated operation. A service session is reused when
    available; decorated free functions can instead supply a bound ``session``
    argument.

    ``attempt_metadata`` and ``terminal_metadata`` are phase-specific field
    derivation callbacks, not automatic state snapshots. The decorator
    deliberately does not serialize or diff function arguments and return
    values because those objects can contain secrets, workflow inputs and
    outputs, prompts, tool results, or uploaded content. Callbacks expose only
    explicitly selected audit metadata.

    Args:
        resource_type: Type of resource being changed, such as ``"workflow"``
            or ``"schedule"``. This is stored on every emitted event and also
            selects the default resource ID attribute.
        action: Default action stored on every emitted event. An
            ``attempt_metadata`` callback can replace it when the action depends
            on the call's input.
        resource_id_attr: Name of the UUID attribute or function parameter from
            which to extract the affected resource ID. When omitted, the name is
            looked up from ``resource_type`` and falls back to ``"id"``. Before
            the call, extraction checks positional objects and bound arguments;
            after the call, the return value is checked first.
        attempt_metadata: Optional synchronous or asynchronous callback invoked
            before the attempt event. It takes the decorated function's own
            arguments and returns :class:`AuditEventDetails`. Only ``action``,
            ``resource_id``, ``data``, and ``emit`` are used in this phase. If it
            raises, the decorator logs the error and continues with its default
            fields.
        terminal_metadata: Optional synchronous or asynchronous callback invoked
            after the decorated function returns. It takes the result followed by
            the decorated function's arguments; method-attached callbacks should
            declare ``(result, *args: Any, **kwargs: Any)`` because a named
            parameter spelling fails pyright's name check against P. Only
            ``resource_id``, ``data``, and ``status`` are used in this phase. If
            it raises, the terminal event uses the fields already derived.

    Returns:
        A decorator that preserves the wrapped function's signature and return
        value while adding the audit lifecycle.
    """

    def decorator(
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        # Determine the resource_id_attr from mapping or use provided/default
        resolved_resource_id_attr = (
            resource_id_attr
            if resource_id_attr is not None
            else _RESOURCE_ID_ATTR_MAP.get(resource_type, "id")
        )
        signature = inspect.signature(func)

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound_arguments: Mapping[str, Any] = {}
            try:
                bound_arguments = signature.bind_partial(*args, **kwargs).arguments
            except TypeError as exc:
                logger.warning(
                    "Audit argument binding failed",
                    error=redact_sensitive_text(str(exc), redact_emails=True),
                )

            first_arg = args[0] if args else None
            service = (
                first_arg
                if isinstance(first_arg, BaseService) or hasattr(first_arg, "role")
                else bound_arguments.get("service")
            )
            candidate = getattr(service, "role", None) or bound_arguments.get("role")
            role: Role | PlatformRole | None = (
                candidate
                if isinstance(candidate, (Role, PlatformRole))
                else ctx_role.get()
            )

            if role is None or role.actor_id is None:
                return await func(*args, **kwargs)

            raw_session = getattr(service, "session", None)
            if raw_session is None:
                raw_session = bound_arguments.get("session")
            session = cast(AsyncSession | None, raw_session)

            resource_id: uuid.UUID | None = None
            try:
                resource_id = _extract_resource_id(
                    args,
                    kwargs,
                    None,
                    resolved_resource_id_attr,
                    bound_arguments=bound_arguments,
                )
            except Exception as exc:
                logger.warning(
                    "Audit resource_id extraction failed",
                    error=redact_sensitive_text(str(exc), redact_emails=True),
                )

            event_action = action
            event_data: AuditMetadata | None = None
            should_emit = True
            if attempt_metadata is not None:
                try:
                    details = attempt_metadata(*args, **kwargs)
                    if inspect.isawaitable(details):
                        details = await details
                    if details.action is not None:
                        event_action = details.action
                    if details.resource_id is not None:
                        resource_id = details.resource_id
                    event_data = details.data
                    should_emit = details.emit
                except Exception as exc:
                    logger.warning(
                        "Audit attempt metadata derivation failed",
                        resource_type=resource_type,
                        action=action,
                        error_type=type(exc).__name__,
                    )

            if not should_emit:
                return await func(*args, **kwargs)

            async with AuditService.with_session(role, session=session) as svc:
                # Log attempt
                try:
                    await svc.create_event(
                        resource_type=resource_type,
                        action=event_action,
                        resource_id=resource_id,
                        status=AuditEventStatus.ATTEMPT,
                        data=dict(event_data) if event_data is not None else None,
                    )
                except Exception as exc:
                    logger.warning(
                        "Audit attempt log failed",
                        error=redact_sensitive_text(str(exc), redact_emails=True),
                    )

            # NOTE: Do not execute the function while holding the audit service session open
            try:
                # Execute the actual function
                result = await func(*args, **kwargs)

                # Log success, or a semantic failure for result objects that
                # complete without raising but report success=False.
                try:
                    terminal_resource_id = _extract_resource_id(
                        args,
                        kwargs,
                        result,
                        resolved_resource_id_attr,
                        bound_arguments=bound_arguments,
                    )
                    if terminal_resource_id is None:
                        terminal_resource_id = resource_id
                    terminal_data = event_data
                    terminal_status = (
                        AuditEventStatus.FAILURE
                        if (
                            getattr(result, "success", None) is False
                            or getattr(result, "ok", None) is False
                        )
                        else AuditEventStatus.SUCCESS
                    )
                    if terminal_metadata is not None:
                        try:
                            details = terminal_metadata(result, *args, **kwargs)
                            if inspect.isawaitable(details):
                                details = await details
                            if details.resource_id is not None:
                                terminal_resource_id = details.resource_id
                            if details.data is not None:
                                terminal_data = details.data
                            if details.status is not None:
                                terminal_status = details.status
                        except Exception as exc:
                            logger.warning(
                                "Audit terminal metadata derivation failed",
                                resource_type=resource_type,
                                action=event_action,
                                error_type=type(exc).__name__,
                            )
                    async with AuditService.with_session(role, session=session) as svc:
                        await svc.create_event(
                            resource_type=resource_type,
                            action=event_action,
                            resource_id=terminal_resource_id,
                            status=terminal_status,
                            data=(
                                dict(terminal_data)
                                if terminal_data is not None
                                else None
                            ),
                        )
                except Exception as exc:
                    logger.warning(
                        "Audit success log failed",
                        error=redact_sensitive_text(str(exc), redact_emails=True),
                    )
                return result
            except Exception:
                # Log failure
                try:
                    async with AuditService.with_session(role, session=None) as svc:
                        await svc.create_event(
                            resource_type=resource_type,
                            action=event_action,
                            resource_id=resource_id,
                            status=AuditEventStatus.FAILURE,
                            data=dict(event_data) if event_data is not None else None,
                        )
                except Exception as exc:
                    logger.warning(
                        "Audit failure log failed",
                        error=redact_sensitive_text(str(exc), redact_emails=True),
                    )
                raise

        return wrapper

    return decorator


def _extract_resource_id(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any | None,
    attr: str,
    *,
    bound_arguments: Mapping[str, Any] | None = None,
) -> uuid.UUID | None:
    """Extract a resource ID from an audited call or its result.

    Values are considered in this order:

    1. The return value's ``attr`` attribute.
    2. The first positional argument with an ``attr`` attribute.
    3. The bound function argument named ``attr``.
    4. The keyword argument named ``attr``.

    Args:
        args: Positional arguments passed to the decorated function.
        kwargs: Keyword arguments passed to the decorated function.
        result: Return value of the decorated function, or ``None`` before the
            function has run.
        attr: Attribute or parameter name expected to contain the resource ID.
        bound_arguments: Call arguments already mapped to parameter names by
            the decorated function's signature.

    Returns:
        The extracted ID as a UUID, or ``None`` when no value is available.

    Raises:
        TypeError: If the extracted value is not a UUID or string.
        ValueError: If the extracted string is not a valid UUID.
    """

    raw: Any | None = None

    if result is not None and hasattr(result, attr):
        raw = getattr(result, attr)
    else:
        for arg in args:
            if hasattr(arg, attr):
                raw = getattr(arg, attr)
                break
        if raw is None and bound_arguments is not None:
            raw = bound_arguments.get(attr)
        if raw is None and attr in kwargs:
            raw = kwargs.get(attr)

    if raw is None:
        return None

    return _coerce_uuid(raw, attr)


def _coerce_uuid(value: Any, source: str) -> uuid.UUID | None:
    """Normalize a resource ID value.

    Args:
        value: A UUID, a stringified UUID, or ``None``.
        source: Attribute or parameter name used to identify invalid values in
            error messages.

    Returns:
        The normalized UUID, or ``None`` when ``value`` is ``None``.

    Raises:
        TypeError: If ``value`` is neither a UUID, string, nor ``None``.
        ValueError: If ``value`` is a string but is not a valid UUID.
    """

    if value is None:
        return None

    if isinstance(value, uuid.UUID):
        return value

    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise ValueError(
                f"Resource ID {source!r} must be a UUID; got invalid string"
            ) from exc

    raise TypeError(
        f"Resource ID {source!r} must be a UUID or stringified UUID, got {type(value).__name__}"
    )
