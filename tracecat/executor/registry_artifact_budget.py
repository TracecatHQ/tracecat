"""Pure cache snapshots and eviction planning for registry artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegistryArtifactCacheEntry:
    """Measured on-disk footprint and recency for one artifact key."""

    cache_key: str
    size_bytes: int
    last_used: float


@dataclass(frozen=True, slots=True)
class RegistryArtifactCacheSnapshot:
    """One internally consistent measurement of cache-owned storage."""

    entries: dict[str, RegistryArtifactCacheEntry]
    structural_bytes: int
    staging_bytes: int
    trash_bytes: int

    @property
    def total_bytes(self) -> int:
        """Return all measured bytes owned by the cache."""
        return (
            self.structural_bytes
            + self.staging_bytes
            + self.trash_bytes
            + sum(entry.size_bytes for entry in self.entries.values())
        )


@dataclass(frozen=True, slots=True)
class RegistryArtifactCacheBudget:
    """Shared entry and byte limits for every enforcement path."""

    max_entries: int
    max_bytes: int
    additional_bytes: int = 0

    def fits(self, *, entry_count: int, total_bytes: int) -> bool:
        """Return whether a measured cache satisfies this budget."""
        return (self.max_entries <= 0 or entry_count <= self.max_entries) and (
            self.max_bytes <= 0 or total_bytes + self.additional_bytes <= self.max_bytes
        )


@dataclass(frozen=True, slots=True)
class RegistryArtifactEvictionPlan:
    """Ordered eligible entries and whether deleting all could fit the budget."""

    candidates: tuple[RegistryArtifactCacheEntry, ...]
    can_fit: bool


def plan_registry_artifact_evictions(
    entries: Mapping[str, RegistryArtifactCacheEntry],
    *,
    total_bytes: int,
    budget: RegistryArtifactCacheBudget,
    excluded: Set[str],
    effective_last_used: Mapping[str, float] | None = None,
) -> RegistryArtifactEvictionPlan:
    """Return one deterministic LRU plan without mutating cache state."""
    recency = effective_last_used or {}
    candidates = tuple(
        sorted(
            (entry for entry in entries.values() if entry.cache_key not in excluded),
            key=lambda entry: recency.get(entry.cache_key, entry.last_used),
        )
    )
    projected_bytes = total_bytes - sum(entry.size_bytes for entry in candidates)
    projected_entries = len(entries) - len(candidates)
    return RegistryArtifactEvictionPlan(
        candidates=candidates,
        can_fit=budget.fits(
            entry_count=projected_entries,
            total_bytes=projected_bytes,
        ),
    )
