"""Enums for custom entities module."""

from enum import StrEnum


class RelationType(StrEnum):
    """Types of relations between entities (for API/models).

    These values are used in the API and models layer to represent
    the type of relationship from the perspective of the source entity.

    Note: We use explicit cardinality terms to avoid ambiguity.
    """

    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"
