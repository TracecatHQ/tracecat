"""Platform skill library: catalog, install state, preset binding, and staging."""

import asyncio
import uuid
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
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import (
    agent_config_from_payload,
    agent_config_to_payload,
)
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset, SkillLibraryInstall, Workspace
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.pagination import CursorPaginationParams
from tracecat.workspace_sync.adapters import AGENT_PRESET_RESOURCE_ADAPTER
from tracecat.workspace_sync.adapters import agent_preset as agent_preset_adapter
from tracecat.workspace_sync.adapters.agent_preset import AgentPresetAdapter
from tracecat.workspace_sync.adapters.base import SyncMappingService
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import AgentPresetResourceSpec, WorkspaceSpec

SLUG = "phishing-triage"


def _code(exc: pytest.ExceptionInfo[TracecatValidationError]) -> str:
    assert exc.value.detail is not None
    return exc.value.detail["code"]


def _write_skill(root: Path, slug: str, frontmatter: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody")


def test_bundled_library_is_valid() -> None:
    library = load_library()
    assert SLUG in library
    reserved = {name for s in PLATFORM_SKILLS for name in (s.skill_name, s.asset_name)}
    assert not reserved & library.keys()


@pytest.mark.parametrize(
    ("slug", "frontmatter"),
    [
        ("mismatch", "name: other"),
        ("with-tools", "name: with-tools\nmetadata:\n  tools: [core.http_request]"),
        (PLATFORM_SKILLS[0].skill_name, f"name: {PLATFORM_SKILLS[0].skill_name}"),
    ],
)
def test_catalog_rejects_invalid_entries(
    tmp_path: Path, slug: str, frontmatter: str
) -> None:
    _write_skill(tmp_path, slug, frontmatter)
    with pytest.raises(ValueError):
        load_library_from(tmp_path)


def test_catalog_rejects_supporting_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, "with-reference", "name: with-reference")
    (tmp_path / "with-reference" / "reference.md").write_text("Details")
    with pytest.raises(ValueError, match="only SKILL.md"):
        load_library_from(tmp_path)


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
async def test_sync_preview_blocks_uninstalled_library_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_installs = AsyncMock(return_value=["phishing-triage"])
    monkeypatch.setattr(
        agent_preset_adapter,
        "SkillLibraryService",
        lambda *_args, **_kwargs: SimpleNamespace(missing_installs=missing_installs),
    )
    presets = {
        source_id: AgentPresetResourceSpec(
            id=source_id, name=source_id, slug=source_id, library_skills=skills
        )
        for source_id, skills in {
            "needs-install": ["incident-summary", "phishing-triage"],
            "installed": ["incident-summary"],
        }.items()
    }
    workspace_service = cast(
        SyncMappingService, SimpleNamespace(session=None, role=None)
    )

    diagnostics = await AgentPresetAdapter().library_skill_diagnostics(
        workspace_service, presets
    )

    missing_installs.assert_awaited_once_with(["incident-summary", "phishing-triage"])
    assert [(d.workflow_title, d.error_type) for d in diagnostics] == [
        ("needs-install", "dependency")
    ]
    assert diagnostics[0].details == {
        "code": "library_skill_not_installed",
        "slugs": ["phishing-triage"],
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
    staged = (plugin / "skills" / SLUG / "SKILL.md").read_bytes()
    assert staged == load_library()[SLUG].markdown


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
def library_service(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> SkillLibraryService:
    # Class-level so the preset service's own library service is entitled too.
    monkeypatch.setattr(
        SkillLibraryService, "has_entitlement", AsyncMock(return_value=True)
    )
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

    async def test_install_requires_agent_addons(
        self,
        library_service: SkillLibraryService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            library_service, "has_entitlement", AsyncMock(return_value=False)
        )
        with pytest.raises(EntitlementRequired):
            await library_service.install(SLUG)

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

    async def test_binding_requires_agent_addons(
        self,
        session: AsyncSession,
        svc_role: Role,
        library_service: SkillLibraryService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await library_service.install(SLUG)
        monkeypatch.setattr(
            SkillLibraryService, "has_entitlement", AsyncMock(return_value=False)
        )
        presets = AgentPresetService(session=session, role=svc_role)
        with pytest.raises(EntitlementRequired):
            await presets.create_preset(_preset([SLUG]))

    async def test_fork_creates_editable_workspace_skill(
        self, library_service: SkillLibraryService
    ) -> None:
        skill = await library_service.fork(SLUG)
        assert skill.name == SLUG
        assert skill.current_version_id is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_uninstall_waits_for_concurrent_bind(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uninstall racing a bind sees the committed binding and is rejected."""
    monkeypatch.setattr(
        SkillLibraryService, "has_entitlement", AsyncMock(return_value=True)
    )
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

            uninstall = asyncio.create_task(
                SkillLibraryService(uninstalling, role=role).uninstall(SLUG)
            )
            await asyncio.sleep(0.2)
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
