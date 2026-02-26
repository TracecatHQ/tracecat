from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum reserved for engineering rollouts."""

    AI_RANKING = "ai-ranking"
