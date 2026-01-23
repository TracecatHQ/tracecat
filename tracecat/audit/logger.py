from __future__ import annotations

import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Concatenate, ParamSpec, TypeVar

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditAction, AuditResourceType
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.service import BaseService

P = ParamSpec("P")
R = TypeVar("R")

# Mapping of resource types to their default resource_id_attr names
_RESOURCE_ID_ATTR_MAP: dict[str, str] = {
    "workflow": "workflow_id",
    "workflow_execution": "wf_id",
    "organization_member": "user_id",
    "organization_session": "session_id",
}


def audit_log(
    *,
    resource_type: AuditResourceType,
    action: AuditAction,
    resource_id_attr: str | None = None,
) -> Callable[
    [Callable[Concatenate[Any, P], Awaitable[R]]],
    Callable[Concatenate[Any, P], Awaitable[R]],
]:
    """Decorator to emit audit attempt/success/failure around service methods.

    If no user role or session is available, auditing is skipped without failing the call.

    The resource_id_attr is automatically derived from resource_type if not provided,
    using the _RESOURCE_ID_ATTR_MAP. Falls back to "id" if no mapping exists.
    """

    def decorator(
        func: Callable[Concatenate[Any, P], Awaitable[R]],
    ) -> Callable[Concatenate[Any, P], Awaitable[R]]:
        # Determine the resource_id_attr from mapping or use provided/default
        resolved_resource_id_attr = (
            resource_id_attr
            if resource_id_attr is not None
            else _RESOURCE_ID_ATTR_MAP.get(resource_type, "id")
        )

        @functools.wraps(func)
        async def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
            role: Role | None = ctx_role.get()

            # Skip audit if we don't have a user role
            if role is None or role.user_id is None:
                return await func(self, *args, **kwargs)

            # Get existing session. Currently only BaseService has a session.
            session = self.session if isinstance(self, BaseService) else None
            resource_id: uuid.UUID | None = None
            try:
                resource_id = _extract_resource_id(
                    args, kwargs, None, resolved_resource_id_attr
                )
            except Exception as exc:
                logger.warning("Audit resource_id extraction failed", error=str(exc))

            async with AuditService.with_session(role, session=session) as svc:
                # Log attempt
                try:
                    await svc.create_event(
                        resource_type=resource_type,
                        action=action,
                        resource_id=resource_id,
                        status=AuditEventStatus.ATTEMPT,
                    )
                except Exception as exc:
                    logger.warning("Audit attempt log failed", error=str(exc))

            # NOTE: Do not execute the function while holding the audit service session open
            try:
                # Execute the actual function
                result = await func(self, *args, **kwargs)

                # Log success
                try:
                    resource_id = _extract_resource_id(
                        args, kwargs, result, resolved_resource_id_attr
                    )
                    async with AuditService.with_session(role, session=session) as svc:
                        await svc.create_event(
                            resource_type=resource_type,
                            action=action,
                            resource_id=resource_id,
                            status=AuditEventStatus.SUCCESS,
                        )
                except Exception as exc:
                    logger.warning("Audit success log failed", error=str(exc))
                return result
            except Exception:
                # Log failure
                try:
                    async with AuditService.with_session(role, session=session) as svc:
                        await svc.create_event(
                            resource_type=resource_type,
                            action=action,
                            resource_id=resource_id,
                            status=AuditEventStatus.FAILURE,
                        )
                except Exception as exc:
                    logger.warning("Audit failure log failed", error=str(exc))
                raise

        return wrapper

    return decorator


def _extract_resource_id(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any | None,
    attr: str,
) -> uuid.UUID | None:
    """Heuristic to find a resource id.

    Priority:
    1) return value `attr` if present
    2) first arg with `attr`
    3) kwargs value for `attr`
    """

    raw: Any | None = None

    if result is not None and hasattr(result, attr):
        raw = getattr(result, attr)
    else:
        for arg in args:
            if hasattr(arg, attr):
                raw = getattr(arg, attr)
                break
        if raw is None and attr in kwargs:
            raw = kwargs.get(attr)

    if raw is None:
        return None

    return _coerce_uuid(raw, attr)


def _coerce_uuid(value: Any, source: str) -> uuid.UUID | None:
    """Convert UUID-like values to uuid.UUID or None, raising on invalid types."""

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
