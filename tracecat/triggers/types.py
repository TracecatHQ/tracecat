"""Domain helpers shared by the trigger API, ingest and dispatch."""

from __future__ import annotations

import hashlib

import orjson

from tracecat import config
from tracecat.tiers.enums import Entitlement
from tracecat.triggers.enums import EventSourceKind, TriggerKind, TriggerTargetType

# Public ingest path per source kind. Customers are handed URLs under these,
# so a path must not change once released.
INGEST_PATH_BY_SOURCE_KIND: dict[EventSourceKind, str] = {
    EventSourceKind.AWS_SNS: "/ingest/sns",
}

# Which source kinds a trigger kind can read. A trigger kind without a source
# would have no entry.
SOURCE_KINDS_BY_TRIGGER_KIND: dict[TriggerKind, frozenset[EventSourceKind]] = {
    TriggerKind.OBJECT_STORAGE: frozenset({EventSourceKind.AWS_SNS}),
}

# The plan feature each trigger kind needs, or None for a kind every plan has.
# Creating, changing or deleting a trigger (or its bindings) checks its kind's
# entry; reading does not. Entitlements are flat booleans, so a kind maps to
# one flag.
ENTITLEMENT_BY_TRIGGER_KIND: dict[TriggerKind, Entitlement | None] = {
    TriggerKind.OBJECT_STORAGE: Entitlement.OBJECT_TRIGGERS,
    TriggerKind.SCHEDULE: None,
}

# Binding a target means every matching event runs it, so the caller needs
# the right to run it as well as ``trigger:update``.
EXECUTE_SCOPE_BY_TARGET: dict[TriggerTargetType, str] = {
    TriggerTargetType.WORKFLOW: "workflow:execute",
    TriggerTargetType.AGENT: "agent:execute",
}


def event_source_endpoint_url(kind: EventSourceKind, endpoint_token: str) -> str:
    """The HTTPS endpoint a customer points their feed at.

    Built only from the source's kind, its stored token and the public API
    URL, so it stays the same across signing-key rotations and app releases.
    """
    path = INGEST_PATH_BY_SOURCE_KIND[kind]
    return f"{config.TRACECAT__PUBLIC_API_URL.rstrip('/')}{path}/{endpoint_token}"


def build_occurrence_key(
    *, bucket: str, key: str, version_id: str | None, sequencer: str | None
) -> str:
    """Identify one object event, for dedupe and for fixed run IDs.

    S3 gives no event ID that survives redelivery, so the key is built from
    what does: bucket, object key, version and the S3 sequencer. It is hashed
    so the unique index stays fixed-width (object keys run to 1024 bytes).

    Returns:
        A 64-character sha256 hex digest.
    """
    canonical = orjson.dumps([bucket, key, version_id, sequencer])
    return hashlib.sha256(canonical).hexdigest()
