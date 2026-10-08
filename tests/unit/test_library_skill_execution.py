"""Activation guidance preserves immutable library content and tool declarations."""

from pathlib import Path

import pytest

from tracecat.agent.skill.builtin.staging import stage_platform_skill_plugin
from tracecat.agent.skill.frontmatter import parse_skill_markdown
from tracecat.agent.skill.library import execution
from tracecat.agent.skill.library.types import LibrarySkill


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_staging_preserves_upstream_content_and_frontmatter(
    tmp_path: Path, newline: str
) -> None:
    markdown = (
        (
            "---\nname: upstream-triage\ndescription: Triage\n"
            "metadata:\n  tools: [core.http_request]\n---\n"
            "# Upstream procedure\nRun `npm install` before querying alerts.\n"
        )
        .replace("\n", newline)
        .encode()
    )
    files = {
        "SKILL.md": markdown,
        "scripts/helper.js": b"// Upstream helper\n",
        "references/guide.md": b"Upstream reference\n",
        "assets/data.bin": b"\x00\xff",
    }
    skill = LibrarySkill("upstream-triage", "Triage", files)
    original = dict(files)
    plugin = tmp_path / "plugin"
    stage_platform_skill_plugin(
        asset_names=[],
        vendored_root=tmp_path / "missing",
        plugin_root=plugin,
        library_skills=[skill],
    )
    staged = plugin / "skills" / skill.slug
    activated = (staged / "SKILL.md").read_bytes()
    normalized = markdown.decode().replace("\r\n", "\n")
    assert parse_skill_markdown(activated.decode()) == parse_skill_markdown(normalized)
    assert activated.decode().endswith(normalized.split("\n---\n", 1)[1])
    assert activated.index(b"## Tracecat execution notes") < activated.index(
        b"# Upstream procedure"
    )
    assert activated.count(b"## Tracecat execution notes") == 1
    assert b"core.script.run_python" in activated
    assert b"Never bypass tool permissions or approvals" in activated
    assert skill.files == original
    for path, content in original.items():
        if path != "SKILL.md":
            assert (staged / path).read_bytes() == content


def test_activation_rejects_missing_frontmatter() -> None:
    with pytest.raises(ValueError, match="missing frontmatter"):
        execution.render_library_skill_markdown(b"# Upstream instructions")
