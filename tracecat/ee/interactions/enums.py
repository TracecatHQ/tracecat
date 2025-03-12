from enum import StrEnum


class InteractionStatus(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    COMPLETED = "completed"


class InteractionType(StrEnum):
    APPROVAL = "approval"
    RESPONSE = "response"


class InteractionCategory(StrEnum):
    SLACK = "slack"
