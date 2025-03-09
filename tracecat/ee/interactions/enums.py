from enum import StrEnum


class SignalStatus(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    COMPLETED = "completed"
