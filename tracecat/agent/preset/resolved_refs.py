"""Structured provenance for preset resource resolution.

The invariant is value-only provenance: entries describe what resolution saw,
including skipped nodes, without providing a fallback binding path. Callers may
opt out by leaving ``ResolvedRefs`` fields as ``None`` for pre-2.2 histories.
"""

from __future__ import annotations

import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

type SkippedAgentPresetRefReason = Literal["deleted", "unpublished", "not_found"]
type ResolvedRefResourceKind = Literal["preset", "skill", "subagent"]
type ResolvedRefStatus = Literal["ok", "skipped"]


class SkippedAgentPresetRef(BaseModel):
    """Preset-backed subagent ref intentionally omitted from resolution."""

    preset_id: uuid.UUID | None = None
    preset_slug: str | None = None
    reason: SkippedAgentPresetRefReason


class ResolvedRef(BaseModel):
    """One value snapshot entry for a resolved or skipped resource node."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: ResolvedRefResourceKind
    slug: str | None = None
    resource_id: uuid.UUID | None = None
    resolved_version_id: uuid.UUID | None = None
    manifest_sha256: str | None = None
    status: ResolvedRefStatus
    code: SkippedAgentPresetRefReason | None = None
    successor_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _validate_skip_code(self) -> Self:
        """Enforce the persisted format: ``code`` iff ``status == "skipped"``."""

        if self.status == "skipped" and self.code is None:
            raise ValueError("skipped refs must carry a skip code")
        if self.status == "ok" and self.code is not None:
            raise ValueError("resolved refs must not carry a skip code")
        return self


class ResolvedRefs(BaseModel):
    """Append-only resolution snapshot; ``successor_id`` is informational only."""

    model_config = ConfigDict(extra="forbid")

    refs: list[ResolvedRef] = Field(default_factory=list)


def without_subagent_refs(refs: ResolvedRefs | None) -> ResolvedRefs | None:
    """Drop ``subagent``-kind entries from a snapshot.

    Used when a preserved session binding overrides the preset's current
    topology: the runtime pass rebuilds the stored binding verbatim, so the
    root snapshot's subagent entries (the preset's *current* children) must
    not be merged into the turn's provenance as if they ran.
    """

    if refs is None:
        return None
    return ResolvedRefs(
        refs=[ref for ref in refs.refs if ref.resource_kind != "subagent"]
    )


def merge_resolved_refs(*parts: ResolvedRefs | None) -> ResolvedRefs | None:
    """Merge snapshots for one turn with kind-scoped deduplication.

    Only ``subagent`` nodes are re-resolved across activity passes (the root
    pass records a provisional entry; the runtime pass records what was
    actually resolved or restored), so only they merge by node identity —
    ``(resource_id)``, slug fallback — with the later pass replacing the
    earlier entry in place. Subagent nodes seen only in an earlier pass
    survive unchanged (e.g. a root-resolution skip record with no runtime
    counterpart). A later id-bearing subagent entry supersedes a provisional
    slug-only entry for the same slug, but entries with distinct
    ``resource_id`` values never merge via slug — a live successor that
    reused a deleted node's slug is a distinct resource and must not erase
    the deleted node's skip record.

    ``skill`` and ``preset`` entries union with exact-duplicate collapse:
    those kinds are never re-resolved across passes, so same-id entries from
    different passes are distinct tree positions that are both true (the
    root's actually-used skill version must survive alongside a child's
    later resolution of the same skill). Entries with neither
    ``resource_id`` nor ``slug`` are appended as-is. First-seen entry order
    is preserved.
    """

    merged: list[ResolvedRef] = []
    index_by_key: dict[tuple[object, ...], int] = {}
    slug_only_index: dict[str, int] = {}
    seen_exact: set[tuple[object, ...]] = set()
    for part in parts:
        if part is None:
            continue
        for ref in part.refs:
            if ref.resource_kind != "subagent":
                exact = (
                    ref.resource_kind,
                    ref.slug,
                    ref.resource_id,
                    ref.resolved_version_id,
                    ref.manifest_sha256,
                    ref.status,
                    ref.code,
                    ref.successor_id,
                )
                if exact in seen_exact:
                    continue
                seen_exact.add(exact)
                merged.append(ref)
                continue
            if ref.resource_id is not None:
                key = ("id", ref.resource_id)
                if key not in index_by_key and ref.slug is not None:
                    provisional = slug_only_index.pop(ref.slug, None)
                    if provisional is not None:
                        merged[provisional] = ref
                        index_by_key[key] = provisional
                        continue
            elif ref.slug is not None:
                key = ("slug", ref.slug)
            else:
                merged.append(ref)
                continue
            existing = index_by_key.get(key)
            if existing is not None:
                merged[existing] = ref
            else:
                index_by_key[key] = len(merged)
                merged.append(ref)
                if ref.resource_id is None and ref.slug is not None:
                    slug_only_index[ref.slug] = index_by_key[key]
    if not merged:
        return None
    return ResolvedRefs(refs=merged)
