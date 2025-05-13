from enum import StrEnum


class TaskStatus(StrEnum):
    """Task status values."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
