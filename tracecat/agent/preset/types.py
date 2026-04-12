"""Internal types for agent preset services."""

from __future__ import annotations

import uuid
from typing import NamedTuple


class SkillBindingSpec(NamedTuple):
    """Comparable key for one skill binding on a preset head."""

    skill_id: uuid.UUID
    skill_version_id: uuid.UUID
