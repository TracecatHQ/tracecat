"""Tests for staged skill file inventories and Read tool input sanitization."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.common.tool_inputs import sanitize_read_tool_input
from tracecat.agent.executor import activity as activity_module
from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.skill.bindings import ResolvedSkillRef
from tracecat.agent.skill.inventory import (
    READ_TOOL_DEFAULT_LINE_LIMIT,
    SKILL_FILE_INVENTORY_HEADING,
    append_skill_file_inventory,
    collect_skill_files,
    render_skill_file_inventory,
)

pytestmark = pytest.mark.anyio


def _write_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\nUse the references.")
    references = skill_dir / "references"
    references.mkdir()
    (references / "guide.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00" * 64)
    (references / "api.md").write_text("line\n" * 10)
    (references / "dump.txt").write_text("row\n" * (READ_TOOL_DEFAULT_LINE_LIMIT + 1))
    (references / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def test_collect_skill_files_classifies_and_skips_root_manifest(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    _write_skill(skill_dir)

    entries = {entry.relative_path: entry for entry in collect_skill_files(skill_dir)}

    assert "SKILL.md" not in entries
    assert entries["references/guide.pdf"].kind == "pdf"
    assert entries["references/logo.png"].kind == "image"
    assert entries["references/api.md"].kind == "text"
    assert entries["references/api.md"].line_count == 10
    assert entries["references/dump.txt"].line_count == READ_TOOL_DEFAULT_LINE_LIMIT + 1


def test_render_inventory_includes_pdf_and_large_text_guidance(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    _write_skill(skill_dir)

    rendered = render_skill_file_inventory(collect_skill_files(skill_dir))

    assert rendered is not None
    assert rendered.startswith(SKILL_FILE_INVENTORY_HEADING)
    assert "`references/guide.pdf` (pdf" in rendered
    assert 'pages: "1-10"' in rendered
    assert "pdftotext" in rendered
    assert "`offset` and `limit`" in rendered
    assert "`references/dump.txt`" in rendered


def test_render_inventory_is_none_without_supporting_files() -> None:
    assert render_skill_file_inventory([]) is None


def test_append_inventory_writes_once_and_leaves_manifest_only_skills_alone(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    _write_skill(skill_dir)
    manifest = skill_dir / "SKILL.md"
    original = manifest.read_text()

    assert append_skill_file_inventory(skill_dir) is True
    appended = manifest.read_text()
    assert appended.startswith(original)
    assert appended.count(SKILL_FILE_INVENTORY_HEADING) == 1

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("---\nname: bare\n---\nNothing else.")
    assert append_skill_file_inventory(bare) is False
    assert SKILL_FILE_INVENTORY_HEADING not in (bare / "SKILL.md").read_text()


async def test_stage_resolved_skills_appends_inventory_to_staged_copy_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = tmp_path / "cache"
    _write_skill(cached)
    cached_manifest = (cached / "SKILL.md").read_text()
    resolved = ResolvedSkillRef(
        skill_id=uuid.uuid4(),
        skill_name="demo",
        skill_version_id=uuid.uuid4(),
        manifest_sha256="a" * 64,
    )

    @asynccontextmanager
    async def with_session(*, role: object):
        yield object()

    monkeypatch.setattr(activity_module.SkillService, "with_session", with_session)
    executor = SimpleNamespace(
        input=SimpleNamespace(
            config=SimpleNamespace(resolved_skills=[resolved]), role=object()
        ),
        _ensure_cached_skill_dir=AsyncMock(return_value=cached),
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    stage = SandboxedAgentExecutor._stage_resolved_skills.__get__(executor)
    await stage(skills_dir)

    staged = (skills_dir / "demo" / "SKILL.md").read_text()
    assert staged.startswith(cached_manifest)
    assert SKILL_FILE_INVENTORY_HEADING in staged
    assert "references/guide.pdf" in staged
    assert (cached / "SKILL.md").read_text() == cached_manifest


@pytest.mark.parametrize(
    ("tool_input", "expected"),
    [
        (
            {"file_path": "/skills/demo/references/api.md", "pages": ""},
            {"file_path": "/skills/demo/references/api.md"},
        ),
        (
            {"file_path": "/skills/demo/SKILL.md", "offset": None, "limit": None},
            {"file_path": "/skills/demo/SKILL.md"},
        ),
        (
            {"file_path": "/skills/demo/references/api.md", "pages": "1-5"},
            {"file_path": "/skills/demo/references/api.md"},
        ),
        (
            {"file_path": "/skills/demo/references/guide.PDF", "pages": "1-5"},
            {"file_path": "/skills/demo/references/guide.PDF", "pages": "1-5"},
        ),
        (
            {"file_path": "/skills/demo/references/guide.pdf", "pages": "  "},
            {"file_path": "/skills/demo/references/guide.pdf"},
        ),
        (
            {"file_path": "/skills/demo/SKILL.md", "offset": 10, "limit": 50},
            {"file_path": "/skills/demo/SKILL.md", "offset": 10, "limit": 50},
        ),
    ],
)
def test_sanitize_read_tool_input(
    tool_input: dict[str, object], expected: dict[str, object]
) -> None:
    assert sanitize_read_tool_input(tool_input) == expected
