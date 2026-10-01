"""Domain types for registry actions.

This module contains dataclasses and type aliases used by the service layer.
Separated from schemas.py to avoid circular imports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tracecat.registry.versions.schemas import RegistryVersionManifest

type RegistrySource = Literal["org", "platform"]
"""Registry table an action row came from."""


@dataclass(frozen=True, slots=True)
class RepositorySyncOutcome:
    """Commit and version produced by a repository sync."""

    commit_sha: str | None
    version: str | None


@dataclass(slots=True)
class IndexEntry:
    """Data holder for registry index entries from UNION ALL query results.

    Used to convert raw query results into objects compatible with
    RegistryActionReadMinimal.from_index() and RegistryActionRead.from_index_and_manifest().
    """

    id: uuid.UUID
    namespace: str
    name: str
    action_type: str
    description: str
    default_title: str | None
    display_group: str | None
    options: dict
    doc_url: str | None = None
    author: str | None = None
    deprecated: str | None = None
    missing_entitlements: tuple[str, ...] = ()


@dataclass(slots=True)
class IndexedActionResult:
    """Result from index/manifest lookup operations.

    Combines index entry metadata with the full manifest for action resolution.
    """

    index_entry: IndexEntry
    manifest: RegistryVersionManifest
    origin: str
    repository_id: uuid.UUID
    source: RegistrySource
    """Registry table the action came from."""


@dataclass(frozen=True, slots=True)
class MissingActionAttribution:
    """Attribution for actions absent from an index lookup."""

    platform: frozenset[str] = frozenset()
    """Names a platform registry provides in some version."""
    entitlement_denied: frozenset[str] = frozenset()
    """Names whose every current registry row needs a disabled entitlement."""
