from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum reserved for engineering rollouts.

    NOTE: At least one member is required for valid OpenAPI schema generation.
    """

    _PLACEHOLDER = "__placeholder__"
