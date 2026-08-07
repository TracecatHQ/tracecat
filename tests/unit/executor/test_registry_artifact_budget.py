"""Pure registry artifact cache budget policy tests."""

from tracecat.executor.registry_artifact_budget import (
    RegistryArtifactCacheBudget,
    RegistryArtifactCacheEntry,
    plan_registry_artifact_evictions,
)


def _entry(
    cache_key: str, *, size: int, last_used: float
) -> RegistryArtifactCacheEntry:
    return RegistryArtifactCacheEntry(
        cache_key=cache_key,
        size_bytes=size,
        last_used=last_used,
    )


def test_plan_orders_eligible_entries_by_effective_lru() -> None:
    entries = {
        "old-on-disk": _entry("old-on-disk", size=40, last_used=1.0),
        "recent-in-process": _entry("recent-in-process", size=40, last_used=2.0),
        "protected": _entry("protected", size=40, last_used=0.0),
    }

    plan = plan_registry_artifact_evictions(
        entries,
        total_bytes=160,
        budget=RegistryArtifactCacheBudget(max_entries=1, max_bytes=50),
        excluded={"protected"},
        effective_last_used={"old-on-disk": 20.0, "recent-in-process": 10.0},
    )

    assert [entry.cache_key for entry in plan.candidates] == [
        "recent-in-process",
        "old-on-disk",
    ]
    assert plan.can_fit is False


def test_plan_reports_when_eligible_evictions_can_satisfy_budget() -> None:
    entries = {
        "old": _entry("old", size=60, last_used=1.0),
        "new": _entry("new", size=30, last_used=2.0),
    }

    plan = plan_registry_artifact_evictions(
        entries,
        total_bytes=100,
        budget=RegistryArtifactCacheBudget(
            max_entries=1,
            max_bytes=80,
            additional_bytes=10,
        ),
        excluded=set(),
    )

    assert [entry.cache_key for entry in plan.candidates] == ["old", "new"]
    assert plan.can_fit is True
