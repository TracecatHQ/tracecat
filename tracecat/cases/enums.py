from enum import StrEnum


class CasePriority(StrEnum):
    """Case priority values aligned with urgency levels.

    Values:
        UNKNOWN (0): No priority is assigned
        LOW (1): Application or personal procedure is unusable, where a workaround is available or a repair is possible
        MEDIUM (2): Non-critical function or procedure is unusable or hard to use causing operational disruptions with no direct impact on a service's availability. A workaround is available
        HIGH (3): Critical functionality or network access is interrupted, degraded or unusable, having a severe impact on services availability. No acceptable alternative is possible
        CRITICAL (4): Interruption making a critical functionality inaccessible or a complete network interruption causing a severe impact on services availability. There is no possible alternative
        OTHER (99): The priority is not normalized
    """

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    OTHER = "other"


class CaseSeverity(StrEnum):
    """Case severity values aligned with OCSF severity values.

    Values:
        UNKNOWN (0): The event/finding severity is unknown
        INFORMATIONAL (1): Informational message. No action required
        LOW (2): The user decides if action is needed
        MEDIUM (3): Action is required but the situation is not serious at this time
        HIGH (4): Action is required immediately
        CRITICAL (5): Action is required immediately and the scope is broad
        FATAL (6): An error occurred but it is too late to take remedial action
        OTHER (99): The event/finding severity is not mapped
    """

    UNKNOWN = "unknown"
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    FATAL = "fatal"
    OTHER = "other"


class CaseStatus(StrEnum):
    """Case status values aligned with OCSF Incident Finding status."""

    UNKNOWN = "unknown"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"
    OTHER = "other"


class CaseEventType(StrEnum):
    """Case activity type values."""

    CASE_CREATED = "case_created"
    CASE_UPDATED = "case_updated"
    CASE_CLOSED = "case_closed"
    CASE_REOPENED = "case_reopened"
    PRIORITY_CHANGED = "priority_changed"
    SEVERITY_CHANGED = "severity_changed"
    STATUS_CHANGED = "status_changed"
    FIELDS_CHANGED = "fields_changed"
    ASSIGNEE_CHANGED = "assignee_changed"
    ATTACHMENT_CREATED = "attachment_created"
    ATTACHMENT_DELETED = "attachment_deleted"
