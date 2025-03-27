from enum import StrEnum


class CodecMode(StrEnum):
    """The mode to use for encoding."""

    THRESHOLD = "threshold"
    """Encode payloads larger than a threshold."""

    ALWAYS = "always"
    """Always encode payloads."""
