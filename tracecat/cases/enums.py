from enum import StrEnum


class CasePriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseStatus(StrEnum):
    """Case status values aligned with OCSF Incident Finding status."""

    UNKNOWN = "unknown"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"
    OTHER = "other"


class CaseActivityType(StrEnum):
    COMMENT = "comment"
    EVENT = "event"


class CaseEventType(StrEnum):
    STATUS_UPDATE = "status_update"
    PRIORITY_UPDATE = "priority_update"
    SEVERITY_UPDATE = "severity_update"
    FIELD_UPDATE = "field_update"
    COMMENT_CREATE = "comment_create"
    COMMENT_UPDATE = "comment_update"
    COMMENT_DELETE = "comment_delete"
