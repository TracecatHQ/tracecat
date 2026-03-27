from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CatalogSelectionLookup:
    """Natural-key lookup used to resolve a catalog row from request input."""

    source_id: uuid.UUID | None
    model_provider: str
    model_name: str


@dataclass(slots=True)
class LegacyModelRepairSummary:
    migrated_defaults: int = 0
    migrated_presets: int = 0
    migrated_versions: int = 0
    unresolved_defaults: int = 0
    unresolved_presets: int = 0
    unresolved_versions: int = 0
    ambiguous_defaults: int = 0
    ambiguous_presets: int = 0
    ambiguous_versions: int = 0
