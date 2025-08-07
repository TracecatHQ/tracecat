"""Custom entities and fields platform for Tracecat.

This module provides workspace-level custom object definitions without SQL migrations.
Fields are immutable after creation (v1) with soft delete support.
"""

from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType

__all__ = ["CustomEntitiesService", "FieldType"]
