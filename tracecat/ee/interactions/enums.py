from enum import StrEnum


class InteractionStatus(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    ERROR = "error"
    TIMED_OUT = "timed_out"
    COMPLETED = "completed"


class InteractionType(StrEnum):
    APPROVAL = "approval"
    RESPONSE = "response"


class InteractionCategory(StrEnum):
    SLACK = "slack"
