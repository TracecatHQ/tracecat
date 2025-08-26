"""Validation package for custom entities.

This package organizes validation logic by concern and re-exports common
validators so existing imports continue to work:

- Entities: workspace-scoped entity checks and uniqueness.
- Fields: field-level validation and uniqueness checks.
- Records: record existence and data (field value) validation, plus
  nested update planning across relations.
- Relations: relation creation policy checks (cycles, degree limits, etc.).

Note: Nested updates validate and plan modifications to regular fields on
target records reached via existing relation links (e.g., updating a manager's
name from an employee record). Relation links themselves remain immutable here.
"""

from .entities import EntityValidators
from .fields import FieldValidators, validate_default_value_type, validate_enum_options
from .records import NestedUpdateValidator, RecordValidators
from .relations import RelationValidators

__all__ = [
    "EntityValidators",
    "FieldValidators",
    "RecordValidators",
    "RelationValidators",
    "NestedUpdateValidator",
    "validate_default_value_type",
    "validate_enum_options",
]
