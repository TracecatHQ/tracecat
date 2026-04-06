"""Domain types for workspace skills."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvedSkillRef:
    """Exact published skill version resolved for agent execution."""

    skill_id: uuid.UUID
    skill_slug: str
    skill_version_id: uuid.UUID
    manifest_sha256: str
