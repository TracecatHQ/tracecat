from __future__ import annotations

import uuid
from typing import cast

from tracecat.audit.types import AuditSink

AUDIT_DELIVERY_GROUP = "audit-delivery"
AUDIT_DELIVERY_STREAMS_KEY = "audit:delivery:streams"
AUDIT_DELIVERY_STREAM_PREFIX = "audit:delivery"
AUDIT_PLATFORM_STREAM_ORG_ID = "_"


def audit_delivery_stream_key(
    sink: AuditSink, organization_id: uuid.UUID | None
) -> str:
    org_key = (
        AUDIT_PLATFORM_STREAM_ORG_ID if sink == "platform" else str(organization_id)
    )
    return f"{AUDIT_DELIVERY_STREAM_PREFIX}:{sink}:{org_key}"


def parse_audit_delivery_stream_key(
    stream_key: str,
) -> tuple[AuditSink, uuid.UUID | None] | None:
    parts = stream_key.split(":")
    if len(parts) != 4 or parts[:2] != ["audit", "delivery"]:
        return None
    sink = parts[2]
    org_key = parts[3]
    if sink == "platform" and org_key == AUDIT_PLATFORM_STREAM_ORG_ID:
        return "platform", None
    if sink != "organization":
        return None
    try:
        return cast(AuditSink, "organization"), uuid.UUID(org_key)
    except ValueError:
        return None
