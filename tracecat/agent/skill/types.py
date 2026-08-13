"""Domain types for workspace skills."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvedSkillRef:
    """Exact published skill version resolved for agent execution."""

    skill_id: uuid.UUID
    skill_name: str
    skill_version_id: uuid.UUID
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SkillUploadManifestConstraint:
    """Manifest metadata that a prepared create upload must preserve."""

    name: str
    description: str | None
