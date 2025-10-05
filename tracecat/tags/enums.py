from enum import Enum


class TagScope(str, Enum):
    """Scope specifier for tags, allowing transitional "both" state."""

    WORKFLOW = "workflow"
    CASE = "case"
    BOTH = "both"
