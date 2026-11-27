"""Enums for event sources, triggers and trigger bindings."""

from enum import StrEnum


class EventSourceKind(StrEnum):
    """How a source's events arrive and are verified."""

    AWS_SNS = "aws_sns"


class TriggerKind(StrEnum):
    """What a trigger listens to. Each kind has its own source and config."""

    OBJECT_STORAGE = "object_storage"
    SCHEDULE = "schedule"


class TriggerStatus(StrEnum):
    """Whether a source, trigger or binding takes part in dispatch."""

    ONLINE = "online"
    OFFLINE = "offline"


class SubscriptionStatus(StrEnum):
    """State of the provider subscription that feeds an event source."""

    PENDING = "pending"
    CONFIRMED = "confirmed"


class TriggerTargetType(StrEnum):
    """What a trigger binding starts."""

    WORKFLOW = "workflow"
    AGENT = "agent"


class TriggerErrorCode(StrEnum):
    """Why a trigger operation was refused."""

    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID = "invalid"
