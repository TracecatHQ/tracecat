"""Materialize selected platform skills as an isolated, skill-only plugin."""

import shutil
from collections.abc import Sequence
from pathlib import Path

import orjson
import yaml

from tracecat.agent.skill.builtin import PLATFORM_SKILL_PLUGIN_NAME, PLATFORM_SKILLS
from tracecat.agent.skill.frontmatter import (
    normalize_skill_markdown,
    parse_skill_markdown,
    split_skill_markdown_frontmatter,
)


def stage_platform_skill_plugin(
    *, asset_names: Sequence[str], vendored_root: Path, plugin_root: Path
) -> None:
    """Copy allowlisted image skills without importing plugin hooks or tools."""
    catalog = {skill.asset_name: skill for skill in PLATFORM_SKILLS}
    skills = [catalog[name] for name in dict.fromkeys(asset_names) if name in catalog]
    if not skills:
        return
    if not vendored_root.is_dir():
        raise FileNotFoundError(
            f"Vendored copilot skills directory is required: {vendored_root}"
        )
    for skill in skills:
        source = vendored_root / skill.asset_name
        markdown = normalize_skill_markdown(
            (source / "SKILL.md").read_text(encoding="utf-8")
        )
        frontmatter = parse_skill_markdown(markdown)
        parts = split_skill_markdown_frontmatter(markdown)
        if frontmatter is None or parts is None or frontmatter.name != skill.asset_name:
            raise ValueError(f"Invalid platform skill manifest: {skill.asset_name}")
        destination = plugin_root / "skills" / skill.skill_name
        shutil.copytree(source, destination)
        # The portable name matches its new directory. Authority comes from the
        # catalog/plugin, never a reserved prefix in SKILL.md.
        frontmatter.name = skill.skill_name
        metadata = yaml.safe_dump(
            frontmatter.model_dump(exclude_unset=True), sort_keys=False
        )
        (destination / "SKILL.md").write_text(
            f"---\n{metadata}---\n{parts[1]}", encoding="utf-8"
        )
        # Vendored guidance links to other platform skills by their asset names.
        # Qualify those invocations so a workspace namesake cannot shadow them.
        for document in destination.rglob("*"):
            if document.suffix not in {".md", ".mdx"} or not document.is_file():
                continue
            content = document.read_text(encoding="utf-8")
            for reference in PLATFORM_SKILLS:
                content = content.replace(
                    f"${reference.asset_name}", f"${reference.qualified_name}"
                ).replace(f"`{reference.asset_name}`", f"`{reference.qualified_name}`")
            document.write_text(content, encoding="utf-8")
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_bytes(
        orjson.dumps({"name": PLATFORM_SKILL_PLUGIN_NAME})
    )
