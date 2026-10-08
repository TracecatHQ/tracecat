"""Offline coverage for scripts/skill_library.py archive handling."""

from __future__ import annotations

import io
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
import skill_library as script
from skill_library import extract, render_sources, write_skill

from tracecat import config
from tracecat.agent.skill.library.sources import parse_sources, read_tree, tree_digest
from tracecat.agent.skill.library.types import LibrarySource, LocalLibrarySource

COMMIT = "c" * 40
SKILL_MD = b"---\nname: triage\ndescription: Triage.\n---\nBody\n"


def _source(
    skills: dict[str, str] | None = None, exclude: tuple[str, ...] = ()
) -> LibrarySource:
    return LibrarySource(
        group="Example",
        group_summary="Example",
        group_description="Example skills",
        provider=None,
        repo="example/skills",
        commit=COMMIT,
        license="MIT",
        skills={"triage": "skills/triage"} if skills is None else skills,
        tree_sha256="0" * 64,
        exclude=exclude,
    )


def _archive(files: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"skills-{COMMIT}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(f"skills-{COMMIT}/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buffer.getvalue()


def test_extract_keeps_each_skill_and_the_root_license() -> None:
    archive = _archive(
        {
            "LICENSE": b"MIT text",
            "NOTICE": b"Notice text",
            "README.md": b"repo readme",
            "skills/triage/SKILL.md": SKILL_MD,
            "skills/triage/references/guide.md": b"guide",
            "skills/triage/deck.pptx": b"binary",
            "skills/hunt/SKILL.md": b"hunt skill",
            "skills/unlisted/SKILL.md": b"not selected",
        }
    )

    files = extract(
        archive,
        _source({"triage": "skills/triage", "hunt": "skills/hunt"}, ("*.pptx",)),
    )

    license_files = {"LICENSE": b"MIT text", "NOTICE": b"Notice text"}
    assert files == {
        "triage": {
            "SKILL.md": SKILL_MD,
            "references/guide.md": b"guide",
            **license_files,
        },
        "hunt": {"SKILL.md": b"hunt skill", **license_files},
    }


def test_extract_prefers_the_skill_license() -> None:
    archive = _archive(
        {
            "LICENSE": b"repo license",
            "skills/triage/SKILL.md": SKILL_MD,
            "skills/triage/LICENSE.md": b"skill license",
        }
    )

    files = extract(archive, _source())["triage"]

    assert files["LICENSE.md"] == b"skill license"
    assert "LICENSE" not in files


@pytest.mark.parametrize(
    ("files", "links", "message"),
    [
        ({"LICENSE": b"x"}, None, "no SKILL.md"),
        ({"skills/triage/SKILL.md": SKILL_MD}, None, "no license file"),
        (
            {"LICENSE": b"x", "skills/triage/SKILL.md": SKILL_MD},
            {"skills/triage/escape": "/etc/passwd"},
            "is a link",
        ),
    ],
)
def test_extract_rejects_unsafe_or_incomplete_skills(
    files: dict[str, bytes], links: dict[str, str] | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        extract(_archive(files, links), _source())


def test_write_skill_replaces_stale_files(tmp_path: Path) -> None:
    write_skill(tmp_path, "triage", {"SKILL.md": b"old", "stale.md": b"stale"})
    write_skill(tmp_path, "triage", {"SKILL.md": SKILL_MD})

    assert read_tree(tmp_path / "triage") == {"SKILL.md": SKILL_MD}


def test_write_skill_rejects_symlinked_destination(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "triage").symlink_to(outside)

    with pytest.raises(ValueError, match="is a symlink"):
        write_skill(tmp_path / "library", "triage", {"SKILL.md": SKILL_MD})

    assert not (outside / "SKILL.md").exists()


def test_default_head_pins_the_default_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "repos/example/skills": {"default_branch": "trunk"},
        "repos/example/skills/commits/trunk": {"sha": "b" * 40},
    }
    monkeypatch.setattr(script, "_api", responses.__getitem__)

    assert script.default_head("example/skills") == "b" * 40


def test_update_keeps_the_pin_when_newer_commits_skip_the_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "sources.toml"
    archive = _archive({"LICENSE": b"MIT", "skills/triage/SKILL.md": SKILL_MD})
    source = script._prepare_sync(_source(), archive).source
    monkeypatch.setattr(script, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: [source])
    monkeypatch.setattr(script, "default_head", lambda _: "d" * 40)
    monkeypatch.setattr(script, "download", lambda *_: archive)

    script.cmd_update([])

    assert parse_sources(manifest.read_text()) == [source]
    assert "is current" in capsys.readouterr().out


def test_add_resolves_commit_to_full_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    manifest = tmp_path / "sources.toml"
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: [])
    monkeypatch.setattr(
        script,
        "_api",
        lambda path: (
            {"sha": COMMIT} if path == "repos/example/skills/commits/main" else None
        ),
    )
    downloads: list[str] = []

    def download(_: str, commit: str) -> bytes:
        downloads.append(commit)
        return _archive({"LICENSE": b"MIT", "skills/triage/SKILL.md": SKILL_MD})

    monkeypatch.setattr(script, "download", download)

    script.cmd_add(
        "example/skills",
        ["skills/triage"],
        "Example",
        [],
        "main",
        "MIT",
        "Example",
        "Example",
    )

    assert downloads == [COMMIT]
    [source] = parse_sources(manifest.read_text())
    assert isinstance(source, LibrarySource)
    assert source.commit == COMMIT


def test_render_sources_round_trips() -> None:
    source = _source(
        {"triage": "skills/triage", "hunt": "skills/hunt"},
        exclude=("*.pptx", 'quote"d'),
    )

    assert parse_sources(render_sources([source])) == [source]


@pytest.mark.parametrize("operation", [script.cmd_sync, script.cmd_update])
@pytest.mark.parametrize("keys", [[], ["AWS"], ["Tracecat"]])
def test_sync_and_update_preserve_local_content_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Callable[[Sequence[str]], None],
    keys: list[str],
) -> None:
    root = tmp_path / "library"
    manifest = tmp_path / "sources.toml"
    upstream = replace(_source(), group="AWS")
    local = LocalLibrarySource(
        group="Tracecat",
        group_summary="Tracecat",
        group_description="Tracecat skills",
        provider=None,
        license="AGPL-3.0-only",
        skills={"response": "skills/response"},
    )
    sources = [upstream, local]
    local_files = {
        "SKILL.md": SKILL_MD.replace(b"triage", b"response"),
        "references/evidence.md": b"Locally maintained guidance",
    }
    write_skill(root, "response", local_files)
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: sources.copy())
    monkeypatch.setattr(script, "default_head", lambda _: "d" * 40)
    downloads: list[str] = []

    def download(repo: str, _: str) -> bytes:
        downloads.append(repo)
        return _archive({"LICENSE": b"MIT", "skills/triage/SKILL.md": SKILL_MD})

    monkeypatch.setattr(script, "download", download)
    operation(keys)

    assert downloads == ([] if keys == ["Tracecat"] else ["example/skills"])
    assert read_tree(root / "response") == local_files
    saved = parse_sources(manifest.read_text())
    assert saved[1] == local
    assert (saved[0].group, saved[0].provider) == ("AWS", None)


def test_render_sources_preserves_official_maintenance() -> None:
    source = replace(_source(), group="Example", provider=None)
    rendered = render_sources([source])
    assert "provider =" not in rendered
    assert parse_sources(rendered) == [source]


def test_add_requires_a_description_for_a_new_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(script, "load_sources", list)
    with pytest.raises(SystemExit, match="pass --summary and --description"):
        script.cmd_add("example/skills", ["skills/triage"], "New", [], COMMIT, "MIT")


def test_render_sources_lists_standalone_skills_last() -> None:
    standalone = LocalLibrarySource(
        group=None,
        provider="Tracecat",
        license="AGPL-3.0-only",
        skills={"hunt": "skills/hunt"},
        summaries={"hunt": "Run a hunt"},
    )
    rendered = render_sources([standalone, replace(_source(), group="Zeta")])
    assert rendered.index('group = "Zeta"') < rendered.index('provider = "Tracecat"')
    assert parse_sources(rendered)[1] == standalone


def test_render_sources_preserves_a_maintainer_other_than_the_group() -> None:
    source = replace(_source(), provider="Tracecat")
    rendered = render_sources([source])
    assert 'provider = "Tracecat"' in rendered
    assert parse_sources(rendered) == [source]


@pytest.mark.parametrize(
    "markdown",
    [
        b"---\nname: renamed-triage\n---\nBody",
        b"---\nname: triage\nmetadata:\n  tools: [mcp.example.search]\n---\nBody",
    ],
)
@pytest.mark.parametrize("operation", [script.cmd_sync, script.cmd_update])
def test_invalid_candidate_preserves_all_sources_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    markdown: bytes,
    operation: Callable[[Sequence[str]], None],
) -> None:
    root = tmp_path / "library"
    manifest = tmp_path / "sources.toml"
    sources = [
        _source(),
        replace(
            _source(),
            group="Other",
            repo="example/other",
            skills={"hunt": "skills/hunt"},
        ),
    ]
    write_skill(root, "triage", {"SKILL.md": SKILL_MD, "LICENSE": b"MIT"})
    hunt_md = SKILL_MD.replace(b"triage", b"hunt")
    write_skill(root, "hunt", {"SKILL.md": hunt_md, "LICENSE": b"MIT"})
    original_tree = read_tree(root)
    manifest.write_text(render_sources(sources))
    original_manifest = manifest.read_bytes()
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: sources.copy())
    monkeypatch.setattr(script, "default_head", lambda _: "d" * 40)
    monkeypatch.setattr(
        script,
        "download",
        lambda repo, _: _archive(
            {
                "LICENSE": b"MIT",
                "skills/triage/SKILL.md"
                if repo == "example/skills"
                else "skills/hunt/SKILL.md": SKILL_MD + b"Changed body"
                if repo == "example/skills"
                else markdown.replace(b"name: triage\n", b"name: hunt\n"),
            }
        ),
    )

    with pytest.raises(ValueError):
        operation([])

    assert read_tree(root) == original_tree
    assert manifest.read_bytes() == original_manifest


def test_add_rejects_reserved_name_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    manifest = tmp_path / "sources.toml"
    manifest.write_text(render_sources([]))
    before = manifest.read_bytes()
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: [])
    monkeypatch.setattr(
        script,
        "download",
        lambda *_: _archive(
            {
                "LICENSE": b"MIT",
                "skills/triage/SKILL.md": SKILL_MD.replace(
                    b"triage", b"workspace-chat"
                ),
            }
        ),
    )

    with pytest.raises(ValueError, match="shadows a platform skill"):
        script.cmd_add(
            "example/skills",
            ["skills/triage"],
            "Example",
            [],
            COMMIT,
            "MIT",
            "Example",
            "Example",
        )

    assert not root.exists()
    assert manifest.read_bytes() == before


def test_check_rejects_invalid_content_even_when_digest_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    files = {"SKILL.md": SKILL_MD.replace(b"triage", b"renamed-triage")}
    write_skill(root, "triage", files)
    source = replace(
        _source(),
        tree_sha256=tree_digest(
            {f"triage/{path}": data for path, data in files.items()}
        ),
    )
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "load_sources", lambda: [source])

    with pytest.raises(ValueError, match="frontmatter name must match"):
        script.cmd_check()


def test_sync_publishes_valid_candidate_and_matching_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    manifest = tmp_path / "sources.toml"
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: [_source()])
    monkeypatch.setattr(
        script,
        "download",
        lambda *_: _archive(
            {
                "LICENSE": b"MIT",
                "skills/triage/SKILL.md": SKILL_MD,
            }
        ),
    )

    script.cmd_sync([])

    [source] = parse_sources(manifest.read_text())
    assert isinstance(source, LibrarySource)
    assert source.tree_sha256 == tree_digest(read_tree(root))
    assert read_tree(root / "triage")["SKILL.md"] == SKILL_MD


def test_prepare_rejects_runtime_file_limits_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(script, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(config, "TRACECAT__MAX_SKILL_FILES_COUNT", 1)
    archive = _archive({"LICENSE": b"MIT", "skills/triage/SKILL.md": SKILL_MD})

    with pytest.raises(ValueError, match="too many files"):
        script._prepare_sync(_source(), archive)

    assert not script.LIBRARY_ROOT.exists()


def test_render_sources_quotes_non_ascii_group() -> None:
    source = replace(_source(), group="Café")
    assert parse_sources(render_sources([source])) == [source]


def test_add_extends_a_standalone_source_with_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    manifest = tmp_path / "sources.toml"
    standalone = replace(
        _source(),
        group=None,
        group_summary=None,
        group_description=None,
        provider="Example",
        summaries={"triage": "Triage alerts"},
    )
    monkeypatch.setattr(script, "LIBRARY_ROOT", root)
    monkeypatch.setattr(script, "SOURCES_PATH", manifest)
    monkeypatch.setattr(script, "load_sources", lambda: [standalone])
    monkeypatch.setattr(
        script,
        "download",
        lambda *_: _archive(
            {
                "LICENSE": b"MIT",
                "skills/triage/SKILL.md": SKILL_MD,
                "skills/hunt/SKILL.md": SKILL_MD.replace(b"triage", b"hunt"),
            }
        ),
    )

    with pytest.raises(SystemExit, match="add one PATH with --summary"):
        script.cmd_add("example/skills", ["skills/hunt"], None, [], None, None)

    script.cmd_add(
        "example/skills", ["skills/hunt"], None, [], None, None, None, "Run a hunt"
    )

    [source] = parse_sources(manifest.read_text())
    assert source.summaries == {"triage": "Triage alerts", "hunt": "Run a hunt"}
