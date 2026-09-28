"""Server-owned catalog for image-vendored platform skills.

Catalog keys identify immutable image assets. Portable names and origin are
modeled separately, so workspace skills may use the same names. The executor
stages platform skills as a skill-only Claude plugin, outside workspace skills.
"""

from dataclasses import dataclass
from typing import Literal

from tracecat.agent.skill.types import SkillOrigin

PLATFORM_SKILL_PLUGIN_NAME = "tracecat"
PLATFORM_SKILL_PLUGIN_DIR = "platform-skills"


@dataclass(frozen=True, slots=True)
class PlatformSkillRef:
    """Trusted image asset and its origin-qualified runtime identity."""

    asset_name: str
    skill_name: str
    origin: Literal[SkillOrigin.PLATFORM] = SkillOrigin.PLATFORM

    @property
    def qualified_name(self) -> str:
        """Return the Claude plugin skill reference."""
        return f"{PLATFORM_SKILL_PLUGIN_NAME}:{self.skill_name}"


PLATFORM_SKILLS: tuple[PlatformSkillRef, ...] = (
    PlatformSkillRef("tracecat-workspace-chat", "workspace-chat"),
    PlatformSkillRef("tracecat-automation-best-practices", "automation-best-practices"),
    PlatformSkillRef("tracecat-slackbot-best-practices", "slackbot-best-practices"),
)

# Payloads retain stable image asset keys. Only this server-owned catalog can
# interpret a key as platform origin; workspace frontmatter cannot claim it.
BUILTIN_WORKSPACE_CHAT_SKILLS = tuple(skill.asset_name for skill in PLATFORM_SKILLS)
