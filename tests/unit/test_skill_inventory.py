"""Tests for staged skill file inventories and Read tool input sanitization."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from tracecat.agent.common.tool_inputs import sanitize_read_tool_input
from tracecat.agent.executor import activity as activity_module
from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.sandbox.tool_use_rewrite import (
    ToolUseStreamRewriter,
    sanitize_messages_response_body,
)
from tracecat.agent.skill import inventory as inventory_module
from tracecat.agent.skill.bindings import ResolvedSkillRef
from tracecat.agent.skill.inventory import (
    READ_TOOL_DEFAULT_LINE_LIMIT,
    SKILL_FILE_INVENTORY_HEADING,
    SkillFileInventory,
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
    (references / "blob.bin").write_bytes(b"\xff" + b"ok" * 8)
    # Odd-length ASCII prefix so a multi-byte char straddles the sniff window.
    (references / "utf8.md").write_bytes(b"a" + "é".encode() * 5000)


def test_collect_skill_files_classifies_and_skips_root_manifest(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    _write_skill(skill_dir)

    inventory = collect_skill_files(skill_dir)
    entries = {entry.relative_path: entry for entry in inventory.entries}

    assert inventory.omitted == 0
    assert "SKILL.md" not in entries
    assert entries["references/guide.pdf"].kind == "pdf"
    assert entries["references/logo.png"].kind == "image"
    assert entries["references/blob.bin"].kind == "binary"
    assert entries["references/utf8.md"].kind == "text"
    assert entries["references/api.md"].kind == "text"
    assert entries["references/api.md"].line_count == 10
    assert entries["references/dump.txt"].line_count == READ_TOOL_DEFAULT_LINE_LIMIT + 1


def test_collect_skill_files_only_inspects_displayed_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n")
    for index in range(5):
        (skill_dir / f"file{index}.txt").write_text("x\n")
    monkeypatch.setattr(inventory_module, "MAX_INVENTORY_ENTRIES", 2)
    opened: list[Path] = []
    original_sniff = inventory_module._sniff_is_text

    def tracking_sniff(path: Path) -> bool:
        opened.append(path)
        return original_sniff(path)

    monkeypatch.setattr(inventory_module, "_sniff_is_text", tracking_sniff)

    inventory = collect_skill_files(skill_dir)

    assert len(inventory.entries) == 2
    assert inventory.omitted == 3
    assert len(opened) == 2
    rendered = render_skill_file_inventory(inventory)
    assert rendered is not None
    assert "... and 3 more files" in rendered


def test_large_text_files_skip_line_counting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n")
    (skill_dir / "huge.log").write_text("row\n" * 100)
    monkeypatch.setattr(inventory_module, "MAX_LINE_COUNT_BYTES", 16)

    [entry] = collect_skill_files(skill_dir).entries

    assert entry.kind == "text"
    assert entry.line_count is None
    assert entry.is_large_text


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
    assert render_skill_file_inventory(SkillFileInventory(entries=[])) is None


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


def _sse(event_type: str, payload: dict[str, object]) -> bytes:
    return f"event: {event_type}\ndata: {orjson.dumps(payload).decode()}\n\n".encode()


def _read_tool_use_stream(partial_json: list[str]) -> bytes:
    frames = [
        _sse("message_start", {"type": "message_start", "message": {"id": "m"}}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Reading"},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {},
                },
            },
        ),
        *(
            _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                },
            )
            for piece in partial_json
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _sse("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(frames)


def _parse_sse(raw: bytes) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for frame in raw.split(b"\n\n"):
        for line in frame.split(b"\n"):
            if line.startswith(b"data:"):
                events.append(orjson.loads(line[5:]))
    return events


def _tool_input_from_events(events: list[dict[str, object]]) -> dict[str, object]:
    fragments: list[str] = []
    for event in events:
        match event:
            case {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"partial_json": str(piece)},
            }:
                fragments.append(piece)
    return orjson.loads("".join(fragments))


@pytest.mark.parametrize("chunk_size", [1, 7, 64, 100_000])
def test_stream_rewriter_strips_blank_pages_from_read(chunk_size: int) -> None:
    raw = _read_tool_use_stream(
        [
            '{"file_path": "/skills/demo/',
            'refs/api.md", "pages": "", ',
            '"offset": null}',
        ]
    )
    rewriter = ToolUseStreamRewriter()
    out = b"".join(
        rewriter.feed(raw[i : i + chunk_size]) for i in range(0, len(raw), chunk_size)
    )
    out += rewriter.flush()

    events = _parse_sse(out)
    assert [event["type"] for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_stop",
    ]
    assert _tool_input_from_events(events) == {"file_path": "/skills/demo/refs/api.md"}


def test_stream_rewriter_passes_valid_read_through_verbatim() -> None:
    raw = _read_tool_use_stream(
        ['{"file_path": "/skills/demo/refs/guide.pdf", ', '"pages": "1-5"}']
    )
    rewriter = ToolUseStreamRewriter()

    assert rewriter.feed(raw) + rewriter.flush() == raw


def test_stream_rewriter_forwards_text_before_tool_block_completes() -> None:
    raw = _read_tool_use_stream(['{"file_path": "/a.md", "pages": ""}'])
    first_start = raw.index(b"event: content_block_start")
    text_stop = raw.index(b"event: content_block_start", first_start + 1)
    rewriter = ToolUseStreamRewriter()

    first = rewriter.feed(raw[:text_stop])
    assert b"Reading" in first
    second = rewriter.feed(raw[text_stop:]) + rewriter.flush()
    assert b'"pages"' not in second


def test_sanitize_non_streaming_messages_response() -> None:
    body = orjson.dumps(
        {
            "type": "message",
            "content": [
                {"type": "text", "text": "ok"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {"file_path": "/a.md", "pages": "", "limit": None},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "Bash",
                    "input": {"command": "ls", "pages": ""},
                },
            ],
        }
    )

    rewritten = orjson.loads(sanitize_messages_response_body(body))

    assert rewritten["content"][1]["input"] == {"file_path": "/a.md"}
    assert rewritten["content"][2]["input"] == {"command": "ls", "pages": ""}
    assert sanitize_messages_response_body(b"not json") == b"not json"
