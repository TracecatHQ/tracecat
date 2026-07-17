"""Privacy policy for metadata attached to audit events."""

from __future__ import annotations

from tracecat.audit.types import AuditMetadata, AuditMetadataValue
from tracecat.sanitization import redact_sensitive_text

# These values describe how an operation happened without copying resource
# content. Stable identifiers are accepted separately by suffix below.
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "auth_method",
        "changed_fields",
        "delete_mode",
        "operation",
        "trigger_type",
        "workflow_status",
    }
)


def sanitize_audit_metadata(
    data: AuditMetadata | None,
) -> dict[str, AuditMetadataValue] | None:
    """Return only operationally necessary, sanitized audit metadata.

    The audit payload accepts stable ``*_id`` identifiers, changed-field names,
    a small set of operation discriminators, boolean state summaries, and
    numeric counts. Unknown keys are dropped. This intentionally excludes raw
    bodies, headers, content, inputs, outputs, prompts, tool results, file data,
    secret-bearing values, before/after snapshots, and arbitrary resource names
    or descriptions.

    String pattern checks are defense-in-depth for credentials embedded in an
    allowed value. A field is dropped when its value requires redaction rather
    than delivering partially altered operational metadata. Pattern matching
    cannot identify arbitrary opaque secrets, so this function must not be used
    to justify passing unrestricted application data into an audit event.

    Args:
        data: Caller-selected metadata for an audit event.

    Returns:
        A sanitized dictionary, or ``None`` when no allowed metadata remains.
    """

    if not data:
        return None

    sanitized: dict[str, AuditMetadataValue] = {}
    for key, value in data.items():
        if not _is_allowed_metadata(key, value):
            continue
        if isinstance(value, str):
            redacted = redact_sensitive_text(value, redact_emails=True)
            if redacted == value:
                sanitized[key] = value
        elif isinstance(value, list):
            safe_items = [
                item
                for item in value
                if redact_sensitive_text(item, redact_emails=True) == item
            ]
            if safe_items:
                sanitized[key] = safe_items
        else:
            sanitized[key] = value
    return sanitized or None


def _is_allowed_metadata(key: str, value: AuditMetadataValue) -> bool:
    """Return whether a metadata field conforms to the audit data policy."""

    if key.endswith("_id"):
        return isinstance(value, str) or value is None
    if key == "changed_fields":
        return isinstance(value, list)
    if key in _ALLOWED_METADATA_KEYS:
        return isinstance(value, str) or value is None
    if key.startswith(("is_", "has_", "uses_")):
        return isinstance(value, bool)
    if key.endswith("_count"):
        return isinstance(value, int) and not isinstance(value, bool)
    return False
