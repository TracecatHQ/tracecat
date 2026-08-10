"""Transport-error classification shared by blob storage layers."""

from __future__ import annotations

from botocore.exceptions import HTTPClientError


def is_retryable_storage_transport_error(exc: BaseException) -> bool:
    """Return true for transient blob-storage transport failures.

    This deliberately excludes S3 service responses, missing objects,
    integrity failures, validation failures, and user/data errors. Exception
    chains are traversed so callers can preserve domain-specific wrappers.
    """
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None:
            continue
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if isinstance(current, HTTPClientError):
            return True
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        stack.append(current.__cause__)
        stack.append(current.__context__)
    return False
