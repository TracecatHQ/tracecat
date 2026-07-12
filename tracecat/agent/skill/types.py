"""Domain types for workspace skills."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

type SkippedSkillRefReason = Literal["deleted", "unpublished"]


@dataclass(frozen=True, slots=True)
class ResolvedSkillRef:
    """Exact published skill version resolved for agent execution."""

    skill_id: uuid.UUID
    skill_name: str
    skill_version_id: uuid.UUID
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SkippedSkillRef:
    """Preset skill resource that was intentionally omitted from resolution."""

    skill_id: uuid.UUID
    skill_name: str
    skill_slug: str | None
    reason: SkippedSkillRefReason


@dataclass(frozen=True, slots=True)
class ResolvedSkillRefsResult:
    """Resolved executable skill refs plus non-fatal skipped refs."""

    refs: list[ResolvedSkillRef]
    skipped: list[SkippedSkillRef]
