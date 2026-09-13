"""Security helpers for audit webhook settings."""

from __future__ import annotations

from typing import cast

from tracecat.network import DisallowedUrlError, HttpOrigin
from tracecat.settings.schemas import AuditSettingsUpdate


def bind_audit_headers_to_webhook_origin[AuditSettingsUpdateT: AuditSettingsUpdate](
    params: AuditSettingsUpdateT,
    *,
    current_url: object,
) -> AuditSettingsUpdateT:
    """Clear retained webhook headers when a URL moves to another origin.

    Callers may continue changing a webhook path or query on the same origin
    without re-entering credentials. A cross-origin change must submit headers
    explicitly so an existing secret cannot be carried to a new destination.
    """
    updates = params.model_dump(exclude_unset=True)
    if (
        "audit_webhook_url" not in updates
        or "audit_webhook_custom_headers" in updates
        or _same_webhook_origin(current_url, updates["audit_webhook_url"])
    ):
        return params
    return cast(
        AuditSettingsUpdateT,
        params.model_copy(update={"audit_webhook_custom_headers": None}),
    )


def _same_webhook_origin(first: object, second: object) -> bool:
    if first == second:
        return True
    try:
        if not isinstance(first, str) or not isinstance(second, str):
            return False
        return HttpOrigin.from_url(first.strip()) == HttpOrigin.from_url(second.strip())
    except DisallowedUrlError:
        return False
