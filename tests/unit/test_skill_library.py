"""Platform skill library: catalog, install state, preset binding, and staging."""

import asyncio
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import orjson
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.tool_policy import resolve_tool_policy
from tracecat.agent.preset.types import PresetToolInputs
from tracecat.agent.skill.builtin import PLATFORM_SKILLS
from tracecat.agent.skill.builtin.staging import stage_platform_skill_plugin
from tracecat.agent.skill.library import catalog
from tracecat.agent.skill.library.catalog import (
    get_library_skills,
    load_library,
    load_library_from,
)
from tracecat.agent.skill.library.execution import render_library_skill_markdown
from tracecat.agent.skill.library.schemas import LibrarySkillSourceRead
from tracecat.agent.skill.library.service import (
    SkillLibraryErrorCode,
    SkillLibraryService,
    _skill_read,
    normalize_library_slugs,
)
from tracecat.agent.skill.library.sources import (
    load_sources,
    read_tree,
    skill_sources,
    tree_digest,
    verify_library,
)
from tracecat.agent.skill.library.sources import parse_sources as parse_manifest
from tracecat.agent.skill.library.types import LibrarySource, LibrarySourceEntry
from tracecat.agent.skill.manifest import normalize_skill_path
from tracecat.agent.skill.service import SkillService
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import (
    agent_config_from_payload,
    agent_config_to_payload,
)
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset, SkillLibraryInstall, Workspace
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.sync import PullOptions
from tracecat.workspace_sync.adapters import AGENT_PRESET_RESOURCE_ADAPTER
from tracecat.workspace_sync.adapters.agent_preset import AgentPresetAdapter
from tracecat.workspace_sync.adapters.base import SyncMappingService
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    WorkspaceManifest,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
)
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import VcsTreeSnapshot

SLUG = "phishing-triage"


def _code(exc: pytest.ExceptionInfo[TracecatValidationError]) -> str:
    assert exc.value.detail is not None
    return exc.value.detail["code"]


def _write_skill(root: Path, slug: str, frontmatter: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody")


BUNDLED_LIBRARY_ROOT = catalog.LIBRARY_ROOT
TOOLED_SLUG = "tooled-skill"
FIXTURE_LIBRARY_SLUGS = (SLUG, "incident-summary", TOOLED_SLUG)


@pytest.fixture(autouse=True)
def fixture_library(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Serve fixture entries that exercise resources and declared tools."""
    root = tmp_path_factory.mktemp("skill-library")
    for slug in (SLUG, "incident-summary"):
        _write_skill(root, slug, f"name: {slug}\ndescription: Fixture {slug}.")
    _write_skill(
        root,
        TOOLED_SLUG,
        f"name: {TOOLED_SLUG}\nmetadata:\n  tools: [core.http_request]",
    )
    (root / SLUG / "references").mkdir()
    (root / SLUG / "references" / "guide.md").write_text("Fixture guide.")
    monkeypatch.setattr(catalog, "LIBRARY_ROOT", root)
    load_library.cache_clear()
    yield
    load_library.cache_clear()


def test_bundled_library_matches_sources() -> None:
    sources = load_sources()
    assert verify_library(BUNDLED_LIBRARY_ROOT, sources) == []
    # Git drops the empty directory, so a checkout may not have it.
    if BUNDLED_LIBRARY_ROOT.is_dir():
        bundled = load_library_from(BUNDLED_LIBRARY_ROOT)
        assert bundled.keys() == skill_sources(sources).keys()


def parse_sources(text: str) -> list[LibrarySourceEntry]:
    """Parse a test manifest, describing every group its sources use."""
    groups = dict.fromkeys(re.findall(r'^group = "([^"]+)"$', text, re.MULTILINE))
    described = "".join(
        f'[groups."{name}"]\nsummary = "{name}"\ndescription = "{name} skills"\n'
        for name in groups
    )
    return parse_manifest(described + text)


def _source_toml(skills: dict[str, str] | None = None, **overrides: str) -> str:
    entry = {
        "kind": "upstream",
        "group": "Example",
        "repo": "example/skills",
        "commit": "a" * 40,
        "license": "MIT",
        "tree_sha256": "b" * 64,
    } | overrides
    skills = {"triage": "skills/triage"} if skills is None else skills
    return (
        "[[source]]\n"
        + "".join(f'{key} = "{value}"\n' for key, value in entry.items())
        + "[source.skills]\n"
        + "".join(f'{slug} = "{path}"\n' for slug, path in skills.items())
    )


def test_parse_sources_reads_entries() -> None:
    [source] = parse_sources(_source_toml())
    assert isinstance(source, LibrarySource)
    assert source.skills == {"triage": "skills/triage"}
    assert source.exclude == ()
    assert (
        source.skill_source("triage").url
        == f"https://github.com/example/skills/tree/{'a' * 40}/skills/triage"
    )


@pytest.mark.parametrize(
    ("skills", "overrides"),
    [
        (None, {"commit": "main"}),
        (None, {"repo": "not-a-repo"}),
        (None, {"tree_sha256": "short"}),
        ({"triage": "skills/../secrets"}, {}),
        ({"triage": "/abs"}, {}),
        ({}, {}),
    ],
)
def test_parse_sources_rejects_invalid_entries(
    skills: dict[str, str] | None, overrides: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        parse_sources(_source_toml(skills, **overrides))


@pytest.mark.parametrize(
    "second",
    [
        _source_toml({"other": "skills/other"}, group="Other"),
        _source_toml(group="Other", repo="other/skills"),
    ],
    ids=["same repo", "same slug"],
)
def test_parse_sources_rejects_duplicates(second: str) -> None:
    with pytest.raises(ValueError, match="listed twice"):
        parse_sources(_source_toml() + second)


def _local_source_toml(provider: str | None = "Tracecat") -> str:
    owner = f'provider = "{provider}"\n' if provider is not None else ""
    return (
        '[[source]]\nkind = "local"\ngroup = "AWS"\n'
        + owner
        + 'license = "AGPL-3.0-only"\n[source.skills]\n'
        + 'incident-summary = "skills/incident-summary"\n'
    )


def test_mixed_sources_share_group_and_preserve_ownership() -> None:
    upstream, local = parse_sources(_source_toml(group="AWS") + _local_source_toml())
    assert upstream.skill_source("triage").group == "AWS"
    provenance = local.skill_source("incident-summary")
    assert provenance.group == "AWS"
    assert provenance.provider == "Tracecat"
    assert provenance.kind == "local"
    assert provenance.repo is None
    assert provenance.commit is None
    assert provenance.url is None


@pytest.mark.parametrize("kind", ["upstream", "local"])
def test_omitted_provider_preserves_official_group(kind: str) -> None:
    manifest = (
        _source_toml(group="AWS")
        if kind == "upstream"
        else _local_source_toml(provider=None)
    )
    [source] = parse_sources(manifest)
    assert source.provider is None
    provenance = source.skill_source(next(iter(source.skills)))
    assert provenance.provider is None
    assert provenance.group == "AWS"


def test_groups_need_a_description_and_a_source() -> None:
    with pytest.raises(ValueError, match="has no description"):
        parse_manifest(_source_toml())
    with pytest.raises(ValueError, match="without sources"):
        parse_manifest(
            '[groups.Example]\nsummary = "x"\ndescription = "x"\n'
            '[groups.Unused]\nsummary = "x"\ndescription = "x"\n' + _source_toml()
        )


def test_group_description_reaches_each_skill() -> None:
    [source] = parse_sources(_source_toml())
    assert source.skill_source("triage").group_description == "Example skills"


def _standalone_toml(summary: str | None = "Summarize an incident") -> str:
    text = _local_source_toml().replace('group = "AWS"\n', "")
    if summary is not None:
        text += f'[source.summaries]\nincident-summary = "{summary}"\n'
    return text


def test_local_source_without_group_lists_standalone_skills() -> None:
    [source] = parse_sources(_standalone_toml())
    provenance = source.skill_source("incident-summary")
    assert (provenance.group, provenance.provider) == (None, "Tracecat")
    assert provenance.summary == "Summarize an incident"


@pytest.mark.parametrize(
    "manifest",
    [
        _standalone_toml(summary=None),
        _standalone_toml(summary="x" * 61),
        _local_source_toml()
        + '[source.summaries]\nincident-summary = "Grouped skills use the group"\n',
    ],
    ids=["missing", "too long", "grouped"],
)
def test_summaries_are_required_only_for_standalone_skills(manifest: str) -> None:
    with pytest.raises(ValueError):
        parse_sources(manifest)


def test_grouped_skills_carry_their_group_summary() -> None:
    [source] = parse_sources(_source_toml())
    provenance = source.skill_source("triage")
    assert (provenance.group_summary, provenance.summary) == ("Example", None)


def test_api_allows_an_omitted_provider() -> None:
    source = LibrarySkillSourceRead(
        repo=None,
        commit=None,
        license="AGPL-3.0-only",
        url=None,
        group="AWS",
        kind="local",
    )
    assert source.provider is None


@pytest.mark.parametrize(
    "manifest",
    [
        _local_source_toml().replace("skills/incident-summary", "skills/triage"),
        _local_source_toml().replace("skills/incident-summary", "../incident-summary"),
        _local_source_toml().replace('kind = "local"', 'kind = "local"\nrepo = "o/r"'),
        _local_source_toml().replace('kind = "local"', 'kind = "upstream"'),
        _local_source_toml(provider="AWS"),
        _source_toml(provider="Example"),
        _source_toml().replace('group = "Example"\n', ""),
        _source_toml().replace('kind = "upstream"\n', ""),
        _local_source_toml(provider=None).replace('group = "AWS"\n', ""),
    ],
)
def test_invalid_local_paths_and_source_kinds_are_rejected(manifest: str) -> None:
    with pytest.raises(ValueError):
        parse_sources(manifest)


def test_sources_allow_repeated_maintainers_but_reject_group_slug_collision() -> None:
    sources = parse_sources(
        _source_toml(group="AWS")
        + _source_toml({"other": "skills/other"}, repo="other/skills", group="AWS")
    )
    assert len(sources) == 2
    with pytest.raises(ValueError, match="conflicting URL slug"):
        parse_sources(
            _source_toml(group="AWS") + _local_source_toml().replace('"AWS"', '"aws"')
        )


def test_local_content_can_change_without_upstream_digest(tmp_path: Path) -> None:
    sources = parse_sources(_local_source_toml())
    assert verify_library(tmp_path, sources) == [
        "AWS: incident-summary missing locally"
    ]
    _write_skill(tmp_path, "incident-summary", "name: incident-summary")
    assert verify_library(tmp_path, sources) == []
    (tmp_path / "incident-summary" / "references").mkdir()
    (tmp_path / "incident-summary" / "references" / "evidence.md").write_text(
        "New guidance"
    )
    assert verify_library(tmp_path, sources) == []
    assert (
        "references/evidence.md"
        in load_library_from(tmp_path)["incident-summary"].files
    )


@pytest.mark.parametrize("provider", ["Tracecat", None])
def test_local_catalog_source_is_exposed_by_api(
    monkeypatch: pytest.MonkeyPatch,
    provider: str | None,
) -> None:
    monkeypatch.setattr(
        catalog, "load_sources", lambda: parse_sources(_local_source_toml(provider))
    )
    entry = _skill_read(load_library()["incident-summary"], installed=False)
    assert entry.source is not None
    assert entry.source.group == "AWS"
    assert entry.source.provider == provider
    assert entry.source.kind == "local"
    assert entry.source.url is None


def test_verify_library_reports_drift(tmp_path: Path) -> None:
    _write_skill(tmp_path, "triage", "name: triage")
    _write_skill(tmp_path, "orphan", "name: orphan")
    digest = tree_digest(
        {
            f"triage/{path}": data
            for path, data in read_tree(tmp_path / "triage").items()
        }
    )
    example, other = parse_sources(
        _source_toml(tree_sha256=digest)
        + _source_toml({"missing": "skills/missing"}, group="Other", repo="other/x")
    )
    assert verify_library(tmp_path, [example, other]) == [
        "orphan: directory has no sources.toml entry",
        "Other: missing not synced; run `just skill-library sync other/x`",
    ]
    (tmp_path / "orphan" / "SKILL.md").unlink()
    (tmp_path / "orphan").rmdir()
    (tmp_path / "triage" / "SKILL.md").write_text("edited by hand")
    assert verify_library(tmp_path, [example]) == [
        f"Example: files differ from example/skills@{'a' * 12}; "
        "run `just skill-library sync example/skills`"
    ]


def test_verify_library_ignores_bytecode(tmp_path: Path) -> None:
    _write_skill(tmp_path, "triage", "name: triage")
    digest = tree_digest(
        {
            f"triage/{path}": data
            for path, data in read_tree(tmp_path / "triage").items()
        }
    )
    (tmp_path / "triage" / "scripts" / "__pycache__").mkdir(parents=True)
    (tmp_path / "triage" / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    [source] = parse_sources(_source_toml(tree_sha256=digest))
    assert verify_library(tmp_path, [source]) == []
    assert (
        "scripts/__pycache__/x.pyc" not in load_library_from(tmp_path)["triage"].files
    )


def test_load_library_attaches_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = parse_sources(_source_toml({SLUG: f"skills/{SLUG}"}))
    monkeypatch.setattr(catalog, "load_sources", lambda: sources)
    load_library.cache_clear()
    library = load_library()
    assert library[SLUG].source == sources[0].skill_source(SLUG)
    assert library["incident-summary"].source is None


def test_load_library_tolerates_missing_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(catalog, "LIBRARY_ROOT", tmp_path / "missing")
    load_library.cache_clear()
    assert load_library() == {}


def test_load_library_reads_library_root() -> None:
    assert sorted(load_library()) == sorted(FIXTURE_LIBRARY_SLUGS)


@pytest.mark.parametrize(
    ("slug", "frontmatter"),
    [
        ("mismatch", "name: other"),
        ("with-mcp", "name: with-mcp\nmetadata:\n  tools: [mcp.jira.search]"),
        (PLATFORM_SKILLS[0].skill_name, f"name: {PLATFORM_SKILLS[0].skill_name}"),
    ],
)
def test_catalog_rejects_invalid_entries(
    tmp_path: Path, slug: str, frontmatter: str
) -> None:
    _write_skill(tmp_path, slug, frontmatter)
    with pytest.raises(ValueError):
        load_library_from(tmp_path)


def test_catalog_keeps_resource_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, "with-reference", "name: with-reference")
    (tmp_path / "with-reference" / "references").mkdir()
    (tmp_path / "with-reference" / "references" / "guide.md").write_text("Details")
    skill = load_library_from(tmp_path)["with-reference"]
    assert set(skill.files) == {"SKILL.md", "references/guide.md"}


def test_catalog_keeps_declared_registry_tools() -> None:
    assert load_library()[TOOLED_SLUG].declared_tools == ("core.http_request",)


def _policy_inputs(namespaces: list[str]) -> PresetToolInputs:
    return PresetToolInputs(
        key=uuid.uuid4(),
        actions=[],
        namespaces=namespaces,
        mcp_integrations=[],
        tool_approvals={},
        skill_version_ids=[],
        library_skills=[TOOLED_SLUG],
    )


def test_tool_policy_grants_library_tools() -> None:
    policy = resolve_tool_policy(_policy_inputs([]), {}, {})
    assert policy.actions == ("core.http_request",)


def test_tool_policy_blocks_library_tools_outside_namespaces() -> None:
    policy = resolve_tool_policy(_policy_inputs(["tools."]), {}, {})
    assert policy.actions == ()
    assert [(s.tool_id, s.skill_name) for s in policy.blocked_tools] == [
        ("core.http_request", TOOLED_SLUG)
    ]


def test_skill_paths_reject_nul_bytes() -> None:
    with pytest.raises(TracecatValidationError, match="NUL"):
        normalize_skill_path("references/a\x00b.md")


def test_normalize_library_slugs_dedupes_and_sorts() -> None:
    assert normalize_library_slugs(["b", "a", "b"]) == ["a", "b"]
    assert normalize_library_slugs(None) == []


def test_sync_import_dedupes_library_skills_like_api_saves() -> None:
    spec = AgentPresetResourceSpec(
        id="agent-preset-triage",
        name="Triage",
        slug="triage",
        library_skills=["phishing-triage", "incident-summary", "phishing-triage"],
    )

    attrs = AgentPresetAdapter()._version_attrs_from_spec(spec)

    assert attrs["library_skills"] == ["incident-summary", "phishing-triage"]


@pytest.mark.anyio
async def test_sync_preview_blocks_only_unknown_library_skills() -> None:
    presets = {
        source_id: AgentPresetResourceSpec(
            id=source_id, name=source_id, slug=source_id, library_skills=skills
        )
        for source_id, skills in {
            "unavailable": ["incident-summary", "unknown-skill"],
            "available": ["incident-summary", "phishing-triage"],
        }.items()
    }
    workspace_service = cast(
        SyncMappingService, SimpleNamespace(session=None, role=None)
    )

    diagnostics = await AgentPresetAdapter().library_skill_diagnostics(
        workspace_service, presets
    )

    assert [(d.workflow_title, d.error_type) for d in diagnostics] == [
        ("unavailable", "dependency")
    ]
    assert diagnostics[0].details == {
        "code": "library_skill_not_found",
        "slugs": ["unknown-skill"],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("slugs", "expected"),
    [(None, None), ([], None), (["b", "a", "b"], ["a", "b"])],
)
async def test_validated_bindings_normalizes_and_maps_empty_to_none(
    monkeypatch: pytest.MonkeyPatch,
    slugs: list[str] | None,
    expected: list[str] | None,
) -> None:
    validate_bindable = AsyncMock()
    monkeypatch.setattr(SkillLibraryService, "validate_bindable", validate_bindable)
    service = object.__new__(SkillLibraryService)

    assert await service.validated_bindings(slugs) == expected
    if expected is None:
        validate_bindable.assert_not_awaited()
    else:
        validate_bindable.assert_awaited_once_with(expected)


def test_unknown_slug_fails_loudly() -> None:
    with pytest.raises(TracecatValidationError) as exc_info:
        get_library_skills(["does-not-exist"])
    assert _code(exc_info) == "library_skill_not_found"


def test_library_skills_stage_without_platform_skills(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    stage_platform_skill_plugin(
        asset_names=[],
        vendored_root=tmp_path / "missing",
        plugin_root=plugin,
        library_skills=get_library_skills([SLUG]),
    )
    manifest = orjson.loads((plugin / ".claude-plugin" / "plugin.json").read_bytes())
    assert manifest == {"name": "tracecat"}
    staged = plugin / "skills" / SLUG
    assert (staged / "SKILL.md").read_bytes() == render_library_skill_markdown(
        load_library()[SLUG].markdown
    )
    assert (staged / "references" / "guide.md").read_text() == "Fixture guide."


@pytest.mark.anyio
async def test_subagent_library_skills_are_staged(tmp_path: Path) -> None:
    child = SimpleNamespace(config=SimpleNamespace(library_skills=[SLUG]))
    fake = SimpleNamespace(
        input=SimpleNamespace(
            config=SimpleNamespace(builtin_skills=None, library_skills=None),
            subagents=[child],
        )
    )
    stage = SandboxedAgentExecutor._stage_builtin_skills.__get__(fake)
    await stage(tmp_path / "plugin")
    assert (tmp_path / "plugin" / "skills" / SLUG / "SKILL.md").is_file()


def test_sandbox_config_carries_library_skills() -> None:
    config = AgentConfig(model_name="m", model_provider="p", library_skills=[SLUG])
    assert SandboxAgentConfig.from_agent_config(config).library_skills == [SLUG]


def test_payload_round_trip_keeps_library_skills() -> None:
    config = AgentConfig(model_name="m", model_provider="p", library_skills=[SLUG])
    restored = agent_config_from_payload(agent_config_to_payload(config))
    assert restored.library_skills == [SLUG]


@pytest.fixture
def library_service(session: AsyncSession, svc_role: Role) -> SkillLibraryService:
    return SkillLibraryService(session=session, role=svc_role)


def _preset(library_skills: list[str] | None) -> AgentPresetCreate:
    return AgentPresetCreate(
        name="Library preset",
        model_name="gpt-4o-mini",
        model_provider="openai",
        library_skills=library_skills,
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestSkillLibraryService:
    async def test_install_is_idempotent_and_listed(
        self, library_service: SkillLibraryService
    ) -> None:
        first = await library_service.install(SLUG)
        second = await library_service.install(SLUG)
        assert first.installed and second.installed

        page = await library_service.list_skills(CursorPaginationParams(limit=100))
        installed = {item.slug for item in page.items if item.installed}
        assert installed == {SLUG}

    async def test_batch_install_is_all_or_nothing(
        self, library_service: SkillLibraryService
    ) -> None:
        with pytest.raises(TracecatNotFoundError):
            await library_service.batch_install([SLUG, "does-not-exist"])
        page = await library_service.list_skills(CursorPaginationParams(limit=100))
        assert not any(item.installed for item in page.items)

        installed = await library_service.batch_install(
            ["incident-summary", SLUG, SLUG]
        )

        assert [skill.slug for skill in installed] == ["incident-summary", SLUG]
        assert all(skill.installed for skill in installed)

    async def test_get_skill_returns_files_and_install_state(
        self, library_service: SkillLibraryService
    ) -> None:
        (catalog.LIBRARY_ROOT / SLUG / "logo.bin").write_bytes(b"\xff\x00")
        load_library.cache_clear()

        before = await library_service.get_skill(SLUG)

        assert not before.installed
        assert {file.path: file.content for file in before.files} == {
            "SKILL.md": f"---\nname: {SLUG}\ndescription: Fixture {SLUG}.\n---\nBody",
            "logo.bin": None,
            "references/guide.md": "Fixture guide.",
        }
        assert [f.size_bytes for f in before.files if f.path == "logo.bin"] == [2]
        await library_service.install(SLUG)
        assert (await library_service.get_skill(SLUG)).installed

    async def test_get_skill_rejects_unknown_slug(
        self, library_service: SkillLibraryService
    ) -> None:
        with pytest.raises(TracecatNotFoundError):
            await library_service.get_skill("does-not-exist")

    async def test_list_returns_upstream_source(
        self,
        library_service: SkillLibraryService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sources = parse_sources(_source_toml({SLUG: f"skills/{SLUG}"}))
        assert isinstance(sources[0], LibrarySource)
        monkeypatch.setattr(catalog, "load_sources", lambda: sources)
        load_library.cache_clear()

        page = await library_service.list_skills(CursorPaginationParams(limit=100))
        items = {item.slug: item for item in page.items}

        assert (sourced := items[SLUG].source) is not None
        assert sourced.model_dump() == {
            "group": "Example",
            "group_summary": "Example",
            "group_description": "Example skills",
            "summary": None,
            "provider": None,
            "repo": sources[0].repo,
            "commit": "a" * 40,
            "license": "MIT",
            "url": sources[0].skill_source(SLUG).url,
            "kind": "upstream",
        }
        assert items["incident-summary"].source is None

    async def test_library_needs_no_entitlement(
        self,
        session: AsyncSession,
        svc_role: Role,
        library_service: SkillLibraryService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The library is open source; only scopes gate install, bind, and fork.
        monkeypatch.setattr(
            SkillLibraryService, "has_entitlement", AsyncMock(return_value=False)
        )
        await library_service.install(SLUG)
        presets = AgentPresetService(session=session, role=svc_role)
        preset = await presets.create_preset(_preset([SLUG]))
        assert preset.library_skills == [SLUG]
        await library_service.fork(SLUG)

    async def test_preset_rejects_uninstalled_library_skill(
        self,
        session: AsyncSession,
        svc_role: Role,
        library_service: SkillLibraryService,
    ) -> None:
        presets = AgentPresetService(session=session, role=svc_role)
        with pytest.raises(TracecatValidationError) as exc_info:
            await presets.create_preset(_preset([SLUG]))
        assert _code(exc_info) == SkillLibraryErrorCode.NOT_INSTALLED.value

    async def test_bound_skill_resolves_and_blocks_uninstall(
        self,
        session: AsyncSession,
        svc_role: Role,
        library_service: SkillLibraryService,
    ) -> None:
        await library_service.install(SLUG)
        presets = AgentPresetService(session=session, role=svc_role)
        preset = await presets.create_preset(_preset([SLUG]))

        config = await presets.resolve_agent_preset_config(preset_id=preset.id)
        assert config.library_skills == [SLUG]

        with pytest.raises(TracecatValidationError) as exc_info:
            await library_service.uninstall(SLUG)
        assert _code(exc_info) == SkillLibraryErrorCode.IN_USE.value

        pinned_version_id = preset.current_version_id
        await presets.update_preset(preset, AgentPresetUpdate(library_skills=[]))
        await library_service.uninstall(SLUG)
        page = await library_service.list_skills(CursorPaginationParams(limit=100))
        assert not any(item.installed for item in page.items)

        # Pinned versions keep their library skills after uninstall.
        pinned = await presets.resolve_agent_preset_config(
            preset_version_id=pinned_version_id
        )
        assert pinned.library_skills == [SLUG]

    async def test_batch_uninstall_rejects_all_if_one_skill_is_bound(
        self,
        session: AsyncSession,
        svc_role: Role,
        library_service: SkillLibraryService,
    ) -> None:
        slugs = [SLUG, "incident-summary", TOOLED_SLUG]
        await library_service.install_many(slugs)
        await session.commit()
        await AgentPresetService(session, role=svc_role).create_preset(
            _preset(["incident-summary"])
        )

        with pytest.raises(TracecatValidationError):
            await library_service.uninstall_many(slugs)
        await session.rollback()

        assert await library_service._installed(slugs) == set(slugs)

    async def test_batch_uninstall_rejects_all_if_one_install_is_missing(
        self,
        library_service: SkillLibraryService,
    ) -> None:
        await library_service.install(SLUG)
        with pytest.raises(TracecatNotFoundError):
            await library_service.uninstall_many([SLUG, "incident-summary"])
        await library_service.session.rollback()
        assert await library_service._installed([SLUG]) == {SLUG}

    async def test_batch_uninstall_removes_every_selected_install(
        self,
        library_service: SkillLibraryService,
    ) -> None:
        slugs = [SLUG, "incident-summary"]
        await library_service.install_many(slugs)
        await library_service.session.commit()
        await library_service.uninstall_many([*slugs, SLUG])
        assert await library_service._installed(slugs) == set()

    @pytest.mark.parametrize("operation", ["install", "fork", "uninstall"])
    async def test_library_writes_require_scopes(
        self, session: AsyncSession, svc_role: Role, operation: str
    ) -> None:
        reader = svc_role.model_copy(update={"scopes": frozenset({"agent:read"})})
        service = SkillLibraryService(session=session, role=reader)
        operations = {
            "install": service.install,
            "fork": service.fork,
            "uninstall": service.uninstall,
        }
        with pytest.raises(ScopeDeniedError):
            await operations[operation](SLUG)

    async def test_reverse_listing_ignores_cursor_past_catalog(
        self, library_service: SkillLibraryService
    ) -> None:
        page = await library_service.list_skills(
            CursorPaginationParams(
                limit=1,
                cursor=BaseCursorPaginator.encode_cursor("zzz"),
                reverse=True,
            )
        )
        assert [item.slug for item in page.items] == [TOOLED_SLUG]
        assert page.has_more is False

    async def test_declared_tools_must_exist_in_registry(
        self, library_service: SkillLibraryService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = AsyncMock(return_value=[])
        monkeypatch.setattr(RegistryActionsService, "list_actions_from_index", index)
        with pytest.raises(TracecatValidationError) as exc_info:
            await library_service.validate_declared_tools([TOOLED_SLUG])
        assert _code(exc_info) == SkillLibraryErrorCode.TOOLS_UNAVAILABLE.value

        index.return_value = [
            (SimpleNamespace(namespace="core", name="http_request"), None)
        ]
        await library_service.validate_declared_tools([TOOLED_SLUG])
        await library_service.validate_declared_tools([SLUG])

    async def test_fork_creates_editable_workspace_skill(
        self, library_service: SkillLibraryService
    ) -> None:
        skill = await library_service.fork(SLUG)
        assert skill.name == SLUG
        assert skill.current_version_id is None
        draft = await SkillService(
            library_service.session, role=library_service.role
        ).get_draft(skill.id)
        assert draft is not None
        assert {file.path for file in draft.files} == {
            "SKILL.md",
            "references/guide.md",
        }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("batch", [False, True])
async def test_uninstall_waits_for_concurrent_bind(svc_role: Role, batch: bool) -> None:
    """An uninstall racing a bind sees the committed binding and is rejected."""
    role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as seed:
            seed.add(
                Workspace(
                    id=role.workspace_id,
                    name="Library lock test",
                    organization_id=role.organization_id,
                )
            )
            await seed.flush()
            seed.add(
                SkillLibraryInstall(workspace_id=role.workspace_id, library_slug=SLUG)
            )
            if batch:
                seed.add(
                    SkillLibraryInstall(
                        workspace_id=role.workspace_id, library_slug="incident-summary"
                    )
                )
            await seed.commit()

        async with sessions() as binding, sessions() as uninstalling:
            await SkillLibraryService(binding, role=role).validate_bindable([SLUG])
            # The bind's shared lock blocks any exclusive lock on the install.
            locked = await uninstalling.scalars(
                sa.select(SkillLibraryInstall.id)
                .where(
                    SkillLibraryInstall.workspace_id == role.workspace_id,
                    SkillLibraryInstall.library_slug == SLUG,
                )
                .with_for_update(skip_locked=True)
            )
            assert locked.all() == []
            await uninstalling.rollback()

            uninstall_pid = await uninstalling.scalar(
                sa.text("SELECT pg_backend_pid()")
            )
            uninstall = asyncio.create_task(
                SkillLibraryService(uninstalling, role=role).uninstall_many(
                    [SLUG, "incident-summary"]
                )
                if batch
                else SkillLibraryService(uninstalling, role=role).uninstall(SLUG)
            )
            # Commit the bind only once uninstall is provably queued on the lock.
            async with sessions() as observer:
                for _ in range(100):
                    wait_event = await observer.scalar(
                        sa.text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :pid"
                        ),
                        {"pid": uninstall_pid},
                    )
                    if wait_event == "Lock":
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("uninstall never waited on the install row lock")
            assert not uninstall.done()

            binding.add(
                AgentPreset(
                    workspace_id=role.workspace_id,
                    name="bound",
                    slug="bound",
                    model_name="test-model",
                    model_provider="test-provider",
                    agents={"subagents": []},
                    library_skills=[SLUG],
                )
            )
            await binding.commit()

            with pytest.raises(TracecatValidationError) as exc_info:
                await uninstall
            assert _code(exc_info) == SkillLibraryErrorCode.IN_USE.value
            await uninstalling.rollback()
            expected = {SLUG, "incident-summary"} if batch else {SLUG}
            assert (
                await SkillLibraryService(uninstalling, role=role)._installed(
                    sorted(expected)
                )
                == expected
            )
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                sa.delete(Workspace).where(Workspace.id == role.workspace_id)
            )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_workspace_sync_round_trips_library_skills(
    session: AsyncSession,
    svc_role: Role,
    library_service: SkillLibraryService,
) -> None:
    await library_service.install(SLUG)
    await AgentPresetService(session=session, role=svc_role).create_preset(
        _preset([SLUG])
    )
    sync = WorkspaceResourceImportService(session=session, role=svc_role)

    projection = await AGENT_PRESET_RESOURCE_ADAPTER.project(sync)
    exported = next(iter(projection.specs.values()))
    assert isinstance(exported, AgentPresetResourceSpec)
    assert exported.library_skills == [SLUG]

    imported_spec = exported.model_copy(
        update={"id": "copy", "slug": "copy", "name": "copy"}
    )
    await sync.import_non_workflow_resources(
        WorkspaceSpec(agent_presets={"copy": imported_spec})
    )
    copied = await session.scalar(
        sa.select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "copy",
        )
    )
    assert copied is not None and copied.library_skills == [SLUG]

    missing = imported_spec.model_copy(update={"library_skills": ["incident-summary"]})
    with pytest.raises(TracecatValidationError) as exc_info:
        await sync.import_non_workflow_resources(
            WorkspaceSpec(agent_presets={"copy": missing})
        )
    assert _code(exc_info) == SkillLibraryErrorCode.NOT_INSTALLED.value


def _library_sync_snapshot(slugs: list[str]) -> WorkspaceRemoteSnapshot:
    return WorkspaceRemoteSnapshot(
        commit_sha="a" * 40,
        files={},
        spec=WorkspaceSpec(
            agent_presets={
                slug: AgentPresetResourceSpec(
                    id=slug, slug=slug, name=slug, library_skills=slugs
                )
                for slug in ("sync-one", "sync-two")
            }
        ),
    )


@pytest.mark.anyio
async def test_pull_previews_installs_without_writes_and_rechecks_on_apply(
    session: AsyncSession,
    svc_role: Role,
    library_service: SkillLibraryService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await library_service.install("incident-summary")
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot = _library_sync_snapshot([SLUG, "incident-summary", SLUG])
    files = service._files_from_spec(manifest=WorkspaceManifest(), spec=snapshot.spec)
    monkeypatch.setattr(
        service,
        "_workspace_git_url",
        AsyncMock(return_value=GitUrl(host="github.com", org="example", repo="sync")),
    )
    transport = SimpleNamespace(
        read_files=AsyncMock(
            return_value=VcsTreeSnapshot(
                commit_sha=snapshot.commit_sha, tree_sha="b" * 40, files=files
            )
        )
    )
    monkeypatch.setattr(service, "_transport_for_provider", lambda: transport)

    preview = await service.pull(
        options=PullOptions(commit_sha=snapshot.commit_sha, dry_run=True)
    )

    assert preview.success, preview.diagnostics
    assert preview.library_skill_installs == [SLUG]
    assert await library_service.missing_installs([SLUG]) == [SLUG]
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentPreset)
            .where(AgentPreset.workspace_id == svc_role.workspace_id)
        )
        == 0
    )
    # An install can disappear after preview. Apply must reconcile the whole
    # dependency set rather than trusting the preview's missing-only list.
    await library_service.uninstall("incident-summary")
    result = await service._import_snapshot(snapshot, sync_schedules=False)

    assert result.success, result.diagnostics
    assert result.library_skill_installs == ["incident-summary", SLUG]
    assert await library_service.missing_installs([SLUG, "incident-summary"]) == []
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(SkillLibraryInstall)
            .where(SkillLibraryInstall.workspace_id == svc_role.workspace_id)
        )
        == 2
    )
    presets = (
        await session.scalars(
            sa.select(AgentPreset).where(
                AgentPreset.workspace_id == svc_role.workspace_id
            )
        )
    ).all()
    assert len(presets) == 2
    assert all(p.library_skills == ["incident-summary", SLUG] for p in presets)
    replay = await service._import_snapshot(snapshot, sync_schedules=False)
    assert replay.success, replay.diagnostics
    assert replay.library_skill_installs == []


@pytest.mark.anyio
async def test_pull_unknown_library_skill_blocks_all_installs(
    session: AsyncSession, svc_role: Role, library_service: SkillLibraryService
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot = _library_sync_snapshot([SLUG, "unknown-skill"])

    prepared = await service._prepare_snapshot_for_import(snapshot)
    assert prepared.library_skill_installs == [SLUG]
    assert all(
        d.details["code"] == "library_skill_not_found" for d in prepared.diagnostics
    )
    result = await service._import_snapshot(snapshot, sync_schedules=False)

    assert not result.success
    assert result.library_skill_installs == []
    assert await library_service.missing_installs([SLUG]) == [SLUG]
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentPreset)
            .where(AgentPreset.workspace_id == svc_role.workspace_id)
        )
        == 0
    )


@pytest.mark.anyio
async def test_pull_rolls_back_library_installs_and_presets_on_late_failure(
    session: AsyncSession,
    svc_role: Role,
    library_service: SkillLibraryService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await library_service.install("incident-summary")
    service = WorkspaceSyncService(session=session, role=svc_role)
    monkeypatch.setattr(
        service,
        "_upsert_mappings",
        AsyncMock(side_effect=TracecatValidationError("Synthetic import failure")),
    )

    result = await service._import_snapshot(
        _library_sync_snapshot([SLUG]), sync_schedules=False
    )

    assert not result.success
    assert result.library_skill_installs == []
    assert await library_service.missing_installs([SLUG, "incident-summary"]) == [SLUG]
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentPreset)
            .where(AgentPreset.workspace_id == svc_role.workspace_id)
        )
        == 0
    )


@pytest.mark.anyio
async def test_pull_library_installs_require_create_scope(
    session: AsyncSession,
    svc_role: Role,
    library_service: SkillLibraryService,
) -> None:
    role = svc_role.model_copy(
        update={"scopes": frozenset({"agent:read", "agent:update"})}
    )
    service = WorkspaceSyncService(session=session, role=role)
    snapshot = _library_sync_snapshot([SLUG])

    # Previews need only read scopes; the install scope gates apply.
    prepared = await service._prepare_snapshot_for_import(snapshot)
    assert prepared.library_skill_installs == [SLUG]
    with pytest.raises(ScopeDeniedError):
        await service._import_snapshot(snapshot, sync_schedules=False)
    assert await library_service.missing_installs([SLUG]) == [SLUG]
