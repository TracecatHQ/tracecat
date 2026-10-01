"""Stable reference identities. URIs are identifiers, never network addresses."""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote, unquote
from uuid import UUID

from tracecat.agent.skill.frontmatter import REGISTRY_TOOL_ID_RE


class ReferenceKind(StrEnum):
    TOOL = "tool"
    MCP_SERVER = "mcp-server"
    MCP_TOOL = "mcp-tool"
    TABLE = "table"
    WORKFLOW = "workflow"
    SKILL = "skill"
    AGENT = "agent"


class ReferenceDiagnosticCode(StrEnum):
    INVALID_URI = "invalid_uri"
    UNSUPPORTED_VERSION = "unsupported_version"
    INVALID_IDENTITY = "invalid_identity"
    INVALID_SOURCE = "invalid_source"
    UNRESOLVED = "unresolved"
    FORBIDDEN = "forbidden"
    CYCLE = "cycle"
    LIMIT_EXCEEDED = "limit_exceeded"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    NESTED_AGENT = "nested_agent"
    SNAPSHOT_CONFLICT = "snapshot_conflict"
    NOT_READY = "not_ready"


class ReferenceURIError(ValueError):
    """A reserved reference URI failed validation with a stable code."""

    def __init__(self, code: ReferenceDiagnosticCode) -> None:
        self.code = code
        super().__init__(code.value)


def is_reference_uri(value: str) -> bool:
    """Recognize the reserved scheme, including malformed/case-varied forms."""
    return value.lower().startswith("tracecat-ref:")


def _validate_identity(
    kind: ReferenceKind, identity: str, tool_name: str | None
) -> None:
    if kind == ReferenceKind.TOOL:
        if (
            len(identity) > 255
            or identity.startswith("mcp.")
            or not REGISTRY_TOOL_ID_RE.fullmatch(identity)
        ):
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_IDENTITY)
    else:
        try:
            canonical = str(UUID(identity))
        except ValueError as exc:
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_IDENTITY) from exc
        if identity != canonical:
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_IDENTITY)
    if kind == ReferenceKind.MCP_TOOL:
        # Same tool-name alphabet as the existing SKILL.md MCP declaration.
        if tool_name is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", tool_name):
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_IDENTITY)
    elif tool_name is not None:
        raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_IDENTITY)


@dataclass(frozen=True, slots=True)
class ReferenceTarget:
    """Stable identity, independent of label, selected version, and workspace lookup."""

    kind: ReferenceKind
    identity: str
    tool_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReferenceKind):
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_IDENTITY)
        _validate_identity(self.kind, self.identity, self.tool_name)


def parse_reference_uri(value: str) -> ReferenceTarget:
    """Validate seven URI forms, decoding segments exactly once.

    Reject URL normalization (authority case, dot segments, queries, fragments),
    encoded separators, controls and double encoding before identity validation.
    """
    if not value.startswith("tracecat-ref://") or re.search(
        r"[\s\x00-\x1f\x7f?#\\]", value
    ):
        raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI)
    parts = value[len("tracecat-ref://") :].split("/")
    if not parts[0]:
        raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI)
    if parts[0] != "v1":
        raise ReferenceURIError(ReferenceDiagnosticCode.UNSUPPORTED_VERSION)
    if len(parts) not in (3, 4):
        raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI)
    try:
        kind = ReferenceKind(parts[1])
    except ValueError as exc:
        raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI) from exc
    if len(parts) != (4 if kind == ReferenceKind.MCP_TOOL else 3):
        raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI)
    decoded: list[str] = []
    for segment in parts[2:]:
        if re.search(r"%(?![0-9a-fA-F]{2})", segment):
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI)
        try:
            item = unquote(segment, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI) from exc
        if not item or re.search(r"[/\\%?#\x00-\x1f\x7f]", item):
            raise ReferenceURIError(ReferenceDiagnosticCode.INVALID_URI)
        decoded.append(item)
    return ReferenceTarget(kind, decoded[0], decoded[1] if len(decoded) == 2 else None)


def serialize_reference_uri(target: ReferenceTarget) -> str:
    """Produce a canonical URI; labels and selected versions are never encoded."""
    _validate_identity(target.kind, target.identity, target.tool_name)
    parts = [target.identity]
    if target.tool_name is not None:
        parts.append(target.tool_name)
    return f"tracecat-ref://v1/{target.kind}/" + "/".join(
        quote(p, safe="-._~") for p in parts
    )
