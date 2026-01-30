"""SDK types for Tracecat API responses.

These types are independent of the tracecat package and mirror the API schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard, final


# === Sentinel Types === #


@final
class Unset:
    """Sentinel type for indicating that a value was not provided.

    This is distinct from None, which may be a valid explicit value.
    Used to distinguish between "user didn't provide this argument" and
    "user explicitly set this to None".

    Example:
        async def update_case(
            self,
            case_id: str,
            *,
            assignee_id: str | None | Unset = UNSET,
        ) -> None:
            data: dict[str, Any] = {}
            # Only include if explicitly provided (including None)
            if is_set(assignee_id):
                data["assignee_id"] = assignee_id
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<UNSET>"

    def __bool__(self) -> bool:
        # Prevent accidental truthiness checks
        return False


UNSET: Unset = Unset()
"""Singleton sentinel value indicating a parameter was not provided."""


def is_set[T](value: T | Unset) -> TypeGuard[T]:
    """Type guard that narrows T | Unset to T.

    Use this to get proper type narrowing when checking if a value was provided.

    Uses both isinstance() and identity checks:
    - isinstance() handles pickled instances (same type, different object)
    - identity handles cross-import edge cases (same object, isinstance may fail)

    Example:
        if is_set(start_time):
            # start_time is narrowed from datetime | Unset to datetime
            data["start_time"] = start_time.isoformat()
    """
    return not (isinstance(value, Unset) or value is UNSET)


# === Case Types === #

CasePriority = Literal[
    "unknown",
    "low",
    "medium",
    "high",
    "critical",
    "other",
]

CaseSeverity = Literal[
    "unknown",
    "informational",
    "low",
    "medium",
    "high",
    "critical",
    "fatal",
    "other",
]

CaseStatus = Literal[
    "unknown",
    "new",
    "in_progress",
    "on_hold",
    "resolved",
    "closed",
    "other",
]


@dataclass
class CaseData:
    """Case data returned from the API."""

    id: str
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    description: str | None = None
    assignee_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    case_number: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    tags: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseData:
        """Create from API response dict."""
        return cls(
            id=str(data["id"]),
            summary=data["summary"],
            status=data["status"],
            priority=data["priority"],
            severity=data["severity"],
            description=data.get("description"),
            assignee_id=str(data["assignee_id"]) if data.get("assignee_id") else None,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            case_number=data.get("case_number"),
            payload=data.get("payload", {}),
            tags=data.get("tags", []),
        )


@dataclass
class CaseCommentData:
    """Case comment data returned from the API."""

    id: str
    content: str
    case_id: str
    user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseCommentData:
        """Create from API response dict."""
        return cls(
            id=str(data["id"]),
            content=data["content"],
            case_id=str(data["case_id"]),
            user_id=str(data["user_id"]) if data.get("user_id") else None,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass
class CaseAttachmentData:
    """Case attachment data returned from the API."""

    id: str
    filename: str
    content_type: str
    size_bytes: int
    case_id: str
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseAttachmentData:
        """Create from API response dict."""
        return cls(
            id=str(data["id"]),
            filename=data["filename"],
            content_type=data["content_type"],
            size_bytes=data["size_bytes"],
            case_id=str(data["case_id"]),
            created_at=data.get("created_at"),
        )


@dataclass
class TagData:
    """Tag data returned from the API."""

    id: str
    name: str
    color: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TagData:
        """Create from API response dict."""
        return cls(
            id=str(data["id"]),
            name=data["name"],
            color=data.get("color"),
        )


# === Table Types === #

SqlType = Literal[
    "TEXT",
    "INTEGER",
    "NUMERIC",
    "DATE",
    "BOOLEAN",
    "TIMESTAMP",
    "TIMESTAMPTZ",
    "JSONB",
    "UUID",
    "SELECT",
    "MULTI_SELECT",
]


@dataclass
class TableData:
    """Table metadata returned from the API."""

    id: str
    name: str
    description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableData:
        """Create from API response dict."""
        return cls(
            id=str(data["id"]),
            name=data["name"],
            description=data.get("description"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass
class TableRowData:
    """Table row data returned from the API."""

    id: str
    data: dict[str, Any]
    table_id: str
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, row_data: dict[str, Any]) -> TableRowData:
        """Create from API response dict."""
        return cls(
            id=str(row_data["id"]),
            data=row_data.get("data", {}),
            table_id=str(row_data["table_id"]),
            created_at=row_data.get("created_at"),
            updated_at=row_data.get("updated_at"),
        )


# === Secret Types === #


@dataclass
class SecretData:
    """Secret data returned from the API (without the actual secret value)."""

    id: str
    name: str
    description: str | None = None
    keys: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecretData:
        """Create from API response dict."""
        return cls(
            id=str(data["id"]),
            name=data["name"],
            description=data.get("description"),
            keys=data.get("keys", []),
        )


@dataclass
class SecretKeyValue:
    """A single key-value pair from a secret."""

    key: str
    value: str


# === Pagination Types === #


@dataclass
class PaginatedResponse[T]:
    """Generic paginated response."""

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False
