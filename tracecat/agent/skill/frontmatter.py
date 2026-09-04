"""Typed parsing for the portable SKILL.md frontmatter contract."""

from __future__ import annotations

import re
from typing import Annotated, Any

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from tracecat.agent.skill.schemas import NewSkillName

MAX_SKILL_TOOLS = 64
MCP_TOOL_ID_RE = re.compile(r"^mcp\.[a-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$")
REGISTRY_TOOL_ID_RE = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")


def _strip_tool_id(value: Any) -> Any:
    """Trim string tool IDs before validating their canonical shape."""

    return value.strip() if isinstance(value, str) else value


def _validate_tool_id(value: str) -> str:
    """Enforce registry and reserved MCP addressing as distinct namespaces."""

    pattern = MCP_TOOL_ID_RE if value.startswith("mcp.") else REGISTRY_TOOL_ID_RE
    if pattern.fullmatch(value) is None:
        raise ValueError("Invalid registry or MCP tool ID")
    return value


ToolId = Annotated[
    str,
    BeforeValidator(_strip_tool_id),
    StringConstraints(
        min_length=3,
        max_length=255,
    ),
    AfterValidator(_validate_tool_id),
]


class SkillMetadata(BaseModel):
    """Tracecat-owned metadata embedded in SKILL.md frontmatter."""

    model_config = ConfigDict(extra="allow")

    tools: list[ToolId] = Field(default_factory=list, max_length=MAX_SKILL_TOOLS)

    @field_validator("tools", mode="after")
    @classmethod
    def dedupe_tools(cls, value: list[ToolId]) -> list[ToolId]:
        """Deduplicate tool declarations without changing their order."""

        return list(dict.fromkeys(value))


class SkillFrontmatter(BaseModel):
    """Portable source-of-truth metadata parsed from a root SKILL.md file."""

    model_config = ConfigDict(extra="allow")

    name: NewSkillName
    description: str | None = Field(default=None)
    metadata: SkillMetadata = Field(default_factory=SkillMetadata)


def normalize_skill_markdown(skill_markdown: str) -> str:
    """Normalize newlines and an optional UTF-8 BOM before parsing."""

    return (
        skill_markdown.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    )


def split_skill_markdown_frontmatter(
    skill_markdown: str,
) -> tuple[str, str] | None:
    """Split normalized root SKILL.md frontmatter from its body."""

    if not skill_markdown.startswith("---\n"):
        return None
    _, _, remainder = skill_markdown.partition("---\n")
    frontmatter, separator, body = remainder.partition("\n---\n")
    if separator:
        return frontmatter, body
    closing_delimiter = "\n---"
    if remainder.endswith(closing_delimiter):
        return remainder[: -len(closing_delimiter)], ""
    return None


def parse_skill_markdown(skill_markdown: str) -> SkillFrontmatter | None:
    """Parse and validate the single authoritative SKILL.md frontmatter model.

    YAML and Pydantic validation errors intentionally propagate to the service
    boundary, where they are converted into the skill API's structured errors.
    """

    normalized = normalize_skill_markdown(skill_markdown)
    parts = split_skill_markdown_frontmatter(normalized)
    if parts is None:
        return None
    frontmatter_yaml, _ = parts
    loaded = yaml.safe_load(frontmatter_yaml)
    return SkillFrontmatter.model_validate(loaded)
