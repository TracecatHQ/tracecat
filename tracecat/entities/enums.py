"""Enums for custom entities module."""

from enum import StrEnum


class RelationKind(StrEnum):
    """Database relation cardinality types.

    These values are stored in the database to represent the actual
    cardinality of relationships between entities.
    """

    ONE_TO_ONE = "one_to_one"  # Represents a one-to-one relationship
    ONE_TO_MANY = "one_to_many"  # Represents a one-to-many relationship


class RelationType(StrEnum):
    """Types of relations between entities (for API/models).

    These values are used in the API and models layer to represent
    the type of relationship from the perspective of the source entity.
    """

    BELONGS_TO = "belongs_to"  # Maps to ONE_TO_ONE in database (source has one target)
    HAS_MANY = "has_many"  # Maps to ONE_TO_MANY in database (source has many targets)
