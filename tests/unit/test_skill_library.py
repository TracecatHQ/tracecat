"""Platform skill library: catalog, install state, preset binding, and staging."""

import asyncio
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
from tracecat import config
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.tool_policy import resolve_tool_policy
from tracecat.agent.preset.types import PresetToolInputs
from tracecat.agent.skill.builtin import PLATFORM_SKILLS
from tracecat.agent.skill.builtin.staging import stage_platform_skill_plugin
from tracecat.agent.skill.library.catalog import (
    get_library_skills,
    load_library,
    load_library_from,
)
from tracecat.agent.skill.library.service import (
    SkillLibraryErrorCode,
    SkillLibraryService,
    normalize_library_slugs,
)
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
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.pagination import CursorPaginationParams
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


VENDORED_LIBRARY_ROOT = Path(config.TRACECAT__SKILL_LIBRARY_DIR)
TOOLED_SLUG = "tooled-skill"
FIXTURE_LIBRARY_SLUGS = (SLUG, "incident-summary", TOOLED_SLUG)


@pytest.fixture(autouse=True)
def fixture_library(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Serve a fixture library; the real one is vendored only into images."""
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
    monkeypatch.setattr(config, "TRACECAT__SKILL_LIBRARY_DIR", str(root))
    load_library.cache_clear()
    yield
    load_library.cache_clear()


def test_vendored_library_is_valid() -> None:
    if not VENDORED_LIBRARY_ROOT.is_dir():
        pytest.skip(
            f"No vendored skill library at {VENDORED_LIBRARY_ROOT}. The "
            "`plugin-skills` Dockerfile stage copies it in at image build time."
        )
    assert load_library_from(VENDORED_LIBRARY_ROOT)


def test_load_library_reads_configured_dir() -> None:
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
    assert (staged / "SKILL.md").read_bytes() == load_library()[SLUG].markdown
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
async def test_uninstall_waits_for_concurrent_bind(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            await seed.commit()

        async with sessions() as binding, sessions() as uninstalling:
            await SkillLibraryService(binding, role=role).validate_bindable([SLUG])
            # The bind's shared lock blocks any exclusive lock on the install.
            locked = await uninstalling.scalars(
                sa.select(SkillLibraryInstall.id)
                .where(SkillLibraryInstall.workspace_id == role.workspace_id)
                .with_for_update(skip_locked=True)
            )
            assert locked.all() == []
            await uninstalling.rollback()

            uninstall_pid = await uninstalling.scalar(
                sa.text("SELECT pg_backend_pid()")
            )
            uninstall = asyncio.create_task(
                SkillLibraryService(uninstalling, role=role).uninstall(SLUG)
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
        await session.scalar(sa.select(sa.func.count()).select_from(AgentPreset)) == 0
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
            sa.select(sa.func.count()).select_from(SkillLibraryInstall)
        )
        == 2
    )
    presets = (await session.scalars(sa.select(AgentPreset))).all()
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
        await session.scalar(sa.select(sa.func.count()).select_from(AgentPreset)) == 0
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
        await session.scalar(sa.select(sa.func.count()).select_from(AgentPreset)) == 0
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
