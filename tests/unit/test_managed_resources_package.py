"""Package-mode export of the managed resources source workspace.

Seeds a source workspace from the expanded sync fixture, marks it as the
managed resources source, and pushes with include patterns. The fixture's
``qa-root`` workflow calls the ``qa-child`` workflow and the
``qa-triage-parent`` preset, reads the ``qa_indicators`` table, a variable and
a secret, and triggers on the ``qa-alert`` case tag. ``qa-orphan`` is
unrelated.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer
from tests.unit.test_workspace_sync_acceptance_contract import (
    _create_workspace_role,
    _expanded_full_git_tree,
    _set_workspace_git_repo_url,
)
from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformSetting,
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    Workspace,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.managed_resources.include import PACKAGE_INCLUDE_FILENAME
from tracecat.managed_resources.source_setting import (
    MANAGED_RESOURCES_SOURCE_KEY,
    ManagedSourceSetting,
)
from tracecat.registry.lock.types import RegistryLock
from tracecat.sync import PullOptions, PushStatus
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowCreate
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.package import (
    package_registry_requirements,
    packaged_action_names,
    read_package,
)
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    SkillResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceSpec,
    WorkspaceSyncExportPreviewRequest,
    WorkspaceSyncExportRequest,
    workspace_manifest_from_json,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService

pytestmark = pytest.mark.usefixtures("db")

REPO_URL = "git+ssh://git@github.com/TracecatHQ/managed-package.git"
GIT_URL = GitUrl(host="github.com", org="TracecatHQ", repo="managed-package")
BRANCH = "package/update"

ROOT_CLOSURE = {
    "workflows/qa-root/definition.yml",
    "workflows/qa-child/definition.yml",
    "agent_presets/qa-triage-parent/preset.yml",
    "tables/qa_indicators/table.yml",
    "variables/default/qa_config.yml",
    "secret_metadata/default/qa_threatintel.yml",
    "case_tags/qa-alert.yml",
}


@dataclass(frozen=True, slots=True)
class SourceHarness:
    service: WorkspaceSyncService
    fake_vcs: FakeVcsServer


@pytest.fixture
async def source(session: AsyncSession, svc_role: Role) -> AsyncIterator[SourceHarness]:
    assert svc_role.workspace_id is not None
    fake_vcs = FakeVcsServer()
    await _set_workspace_git_repo_url(
        session, workspace_id=svc_role.workspace_id, repo_url=REPO_URL
    )
    service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        provider=VcsProvider.GITHUB,
        transport_factory=fake_vcs.transport_factory,
    )
    seed = await fake_vcs.transport_factory(
        VcsProvider.GITHUB, session=session, role=svc_role
    ).write_files(
        url=GIT_URL,
        files=_expanded_full_git_tree(include_schedules=False),
        message="Seed",
        branch="seed",
        create_pr=False,
    )
    assert seed.sha is not None
    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        pulled = await service.pull(options=PullOptions(commit_sha=seed.sha))
        assert pulled.success, pulled.diagnostics
        session.add(
            PlatformSetting(
                key=MANAGED_RESOURCES_SOURCE_KEY,
                value=ManagedSourceSetting(
                    workspace_id=svc_role.workspace_id,
                    git_provider=VcsProvider.GITHUB,
                    git_repo_url=REPO_URL,
                )
                .model_dump_json()
                .encode(),
                value_type="json",
                is_encrypted=False,
            )
        )
        await session.flush()
        yield SourceHarness(service=service, fake_vcs=fake_vcs)


ROOT_SKILL_FILES = {
    "skills/qa-enrichment-skill/skill.yml",
    "skills/qa-enrichment-skill/files/SKILL.md",
    "skills/qa-enrichment-skill/files/enrich.py",
}


def _resource_files(files: dict[str, str]) -> set[str]:
    # Skill and subagent files are asserted on their own: they come in through
    # the preset, and their exact paths are the skill adapter's concern.
    return {
        path
        for path in files
        if path not in (MANIFEST_FILENAME, PACKAGE_INCLUDE_FILENAME)
        and not path.startswith("skills/")
        and not path.startswith("agent_presets/qa-evidence-child")
    }


async def _push(source: SourceHarness, patterns: list[str] | None) -> dict[str, str]:
    result = await source.service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Update package",
            branch=BRANCH,
            package_include=patterns,
        )
    )
    assert result.commit.sha is not None
    return source.fake_vcs.repo_files(GIT_URL, ref=result.commit.sha)


@pytest.mark.anyio
async def test_package_export_writes_only_the_include_closure(
    source: SourceHarness,
) -> None:
    files = await _push(source, ["workflows/qa-root/**"])

    assert _resource_files(files) == ROOT_CLOSURE
    # The triage preset uses a skill and a subagent preset; both ship too.
    assert {path for path in files if path.startswith("skills/")} == ROOT_SKILL_FILES
    assert "agent_presets/qa-evidence-child/preset.yml" in files
    assert "workflows/qa-orphan/definition.yml" not in files
    assert "workflows/qa-root/**" in files[PACKAGE_INCLUDE_FILENAME]
    # Schedules never ship with a package.
    assert "schedules" not in files["workflows/qa-root/definition.yml"]

    package = workspace_manifest_from_json(files[MANIFEST_FILENAME]).package
    assert package is not None
    assert [(e.resource_type, e.source_id) for e in package.explicit] == [
        ("workflow", "qa-root")
    ]
    implied = {(e.resource_type, e.source_id) for e in package.implied}
    assert ("workflow", "qa-child") in implied
    assert ("agent_preset", "qa-triage-parent") in implied
    assert ("table", "qa_indicators") in implied
    assert ("skill", "qa-enrichment-skill") in implied
    assert ("agent_preset", "qa-evidence-child") in implied
    assert package.registry == []


@pytest.mark.anyio
async def test_an_empty_draft_workflow_does_not_block_the_package(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    # A draft with no actions cannot export, but the package does not select
    # it, so discovery must skip it rather than fail the push and the picker.
    await WorkflowsManagementService(session, role=svc_role).create_workflow(
        WorkflowCreate(title="Unfinished draft")
    )

    files = await _push(source, ["workflows/qa-orphan/**"])
    package = await read_package(
        source.service,
        include_text=files[PACKAGE_INCLUDE_FILENAME],
        ref=BRANCH,
    )

    assert _resource_files(files) == {"workflows/qa-orphan/definition.yml"}
    assert all(r.name != "Unfinished draft" for r in package.resources)


@pytest.mark.anyio
async def test_removing_a_pattern_deletes_its_files_in_the_next_commit(
    source: SourceHarness,
) -> None:
    await _push(source, ["workflows/qa-root/**", "workflows/qa-orphan/**"])
    files = await _push(source, ["workflows/qa-orphan/**"])

    assert _resource_files(files) == {"workflows/qa-orphan/definition.yml"}


@pytest.mark.anyio
async def test_push_without_patterns_reuses_the_include_file_on_the_branch(
    source: SourceHarness,
) -> None:
    first = await _push(source, ["workflows/qa-orphan/**"])

    result = await source.service.export_workspace(
        WorkspaceSyncExportRequest(message="Re-push", branch=BRANCH)
    )

    assert result.commit.status is PushStatus.NO_OP
    assert (
        source.fake_vcs.repo_files(GIT_URL, ref=BRANCH)[PACKAGE_INCLUDE_FILENAME]
        == first[PACKAGE_INCLUDE_FILENAME]
    )


@pytest.mark.anyio
async def test_package_preview_reports_what_is_added_and_removed(
    source: SourceHarness,
) -> None:
    await _push(source, ["workflows/qa-orphan/**"])

    preview = await source.service.preview_export_workspace(
        WorkspaceSyncExportPreviewRequest(
            compare_ref=BRANCH, package_include=["workflows/qa-child/**"]
        )
    )

    changes = {(diff.source_path, diff.change_type) for diff in preview.resource_diffs}
    assert ("workflows/qa-orphan/definition.yml", "deleted") in changes
    assert ("workflows/qa-child/definition.yml", "added") in changes


@pytest.mark.anyio
async def test_read_package_marks_explicit_implied_and_excluded(
    source: SourceHarness,
) -> None:
    await _push(source, ["workflows/qa-root/**"])

    package = await source.service.read_package(ref=BRANCH)

    assert package.is_source is True
    assert package.patterns == ["workflows/qa-root/**"]
    by_path = {resource.path: resource for resource in package.resources}
    assert by_path["workflows/qa-root/definition.yml"].status == "explicit"
    child = by_path["workflows/qa-child/definition.yml"]
    assert child.status == "implied"
    assert child.implied_by == ["qa-root-orchestrator"]
    assert by_path["workflows/qa-orphan/definition.yml"].status == "excluded"
    assert by_path["skills/qa-enrichment-skill/skill.yml"].pattern == (
        "skills/qa-enrichment-skill/**"
    )
    assert by_path["case_tags/qa-alert.yml"].pattern == "case_tags/qa-alert.yml"


@pytest.mark.anyio
async def test_unmatched_pattern_is_a_warning(source: SourceHarness) -> None:
    await _push(source, ["workflows/qa-root/**", "workflows/missing/**"])

    package = await source.service.read_package(ref=BRANCH)

    assert package.warnings == ["Pattern 'workflows/missing/**' matches no resource"]


@pytest.mark.anyio
async def test_package_commit_pulls_through_ordinary_git_sync(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    result = await source.service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Package",
            branch=BRANCH,
            package_include=["workflows/qa-root/**"],
        )
    )
    assert result.commit.sha is not None
    other_role = await _create_workspace_role(
        session, source_role=svc_role, workspace_name="plain-pull", repo_url=REPO_URL
    )
    other = WorkspaceSyncService(
        session=session,
        role=other_role,
        provider=VcsProvider.GITHUB,
        transport_factory=source.fake_vcs.transport_factory,
    )

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        pulled = await other.pull(options=PullOptions(commit_sha=result.commit.sha))

    assert pulled.success, pulled.diagnostics


@pytest.mark.anyio
async def test_package_include_is_rejected_outside_the_source_workspace(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    other_role = await _create_workspace_role(
        session, source_role=svc_role, workspace_name="not-source", repo_url=REPO_URL
    )
    other = WorkspaceSyncService(
        session=session,
        role=other_role,
        provider=VcsProvider.GITHUB,
        transport_factory=source.fake_vcs.transport_factory,
    )

    with pytest.raises(TracecatValidationError, match="managed resources source"):
        await other.export_workspace(
            WorkspaceSyncExportRequest(
                message="Nope", branch=BRANCH, package_include=["workflows/**"]
            )
        )


def _spec_using(*actions: str) -> WorkspaceSpec:
    definition = DSLInput(
        title="Uses actions",
        description="",
        entrypoint=DSLEntrypoint(ref="a0", expects={}),
        actions=[
            ActionStatement(ref=f"a{index}", action=action, args={})
            for index, action in enumerate(actions)
        ],
    )
    return WorkspaceSpec(
        workflows={"wf": WorkflowResourceSpec(id="wf", definition=definition)}
    )


async def _custom_action(
    session: AsyncSession, role: Role, *, with_version: bool
) -> RegistryRepository:
    assert role.organization_id is not None
    repo = RegistryRepository(
        organization_id=role.organization_id,
        origin=f"git+ssh://git@github.com/acme/registry-{uuid.uuid4().hex[:8]}.git",
    )
    session.add(repo)
    await session.flush()
    if with_version:
        version = RegistryVersion(
            organization_id=role.organization_id,
            repository_id=repo.id,
            version="1.2.0",
            commit_sha="a" * 40,
            manifest={"actions": {}},
            tarball_uri="s3://registry/acme-1.2.0.tar.gz",
        )
        session.add(version)
        await session.flush()
        repo.current_version_id = version.id
    session.add(
        RegistryAction(
            organization_id=role.organization_id,
            repository_id=repo.id,
            namespace="acme",
            name="enrich_ip",
            description="Enrich an IP",
            origin=repo.origin,
            type="udf",
        )
    )
    await session.flush()
    return repo


@pytest.mark.anyio
async def test_registry_requirements_list_custom_action_repository_version(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    repo = await _custom_action(session, svc_role, with_version=True)

    requirements = await package_registry_requirements(
        source.service,
        _spec_using("core.transform.reshape", "acme.enrich_ip"),
        organization_id=source.service.organization_id,
    )

    assert [(r.origin, r.version, r.commit_sha, r.actions) for r in requirements] == [
        (repo.origin, "1.2.0", "a" * 40, ["acme.enrich_ip"])
    ]


@pytest.mark.anyio
async def test_builtin_actions_need_no_registry(source: SourceHarness) -> None:
    requirements = await package_registry_requirements(
        source.service,
        _spec_using("core.transform.reshape", "core.workflow.execute"),
        organization_id=source.service.organization_id,
    )

    assert requirements == []


@pytest.mark.anyio
async def test_custom_action_without_registry_version_fails_the_export(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    await _custom_action(session, svc_role, with_version=False)

    with pytest.raises(TracecatValidationError, match="acme.enrich_ip"):
        await package_registry_requirements(
            source.service,
            _spec_using("acme.enrich_ip"),
            organization_id=source.service.organization_id,
        )


@pytest.mark.anyio
async def test_registry_requirements_read_the_current_version_index(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    # Repositories synced through the registry API have index rows for the
    # current version and no RegistryAction rows.
    assert svc_role.organization_id is not None
    repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin=f"git+ssh://git@github.com/acme/indexed-{uuid.uuid4().hex[:8]}.git",
    )
    session.add(repo)
    await session.flush()
    version = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        commit_sha="d" * 40,
        manifest={"actions": {}},
        tarball_uri="s3://registry/indexed-2.0.0.tar.gz",
    )
    session.add(version)
    await session.flush()
    repo.current_version_id = version.id
    session.add(
        RegistryIndex(
            organization_id=svc_role.organization_id,
            registry_version_id=version.id,
            namespace="acme",
            name="lookup_hash",
            action_type="udf",
            description="Look up a hash",
            options={"include_in_schema": True},
        )
    )
    await session.flush()

    requirements = await package_registry_requirements(
        source.service,
        _spec_using("acme.lookup_hash"),
        organization_id=source.service.organization_id,
    )

    assert [(r.origin, r.version, r.actions) for r in requirements] == [
        (repo.origin, "2.0.0", ["acme.lookup_hash"])
    ]


@pytest.mark.anyio
async def test_registry_requirements_follow_template_steps_into_other_registries(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    # acme.triage is a template whose step calls acme.enrich_ip, which lives
    # in another repository. Rollout validation resolves template steps, so
    # both repositories must ship.
    step_repo = await _custom_action(session, svc_role, with_version=True)
    assert svc_role.organization_id is not None
    template_repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin=f"git+ssh://git@github.com/acme/templates-{uuid.uuid4().hex[:8]}.git",
    )
    session.add(template_repo)
    await session.flush()
    template = {
        "namespace": "acme",
        "name": "triage",
        "action_type": "template",
        "description": "Triage an alert",
        "interface": {"expects": {}, "returns": None},
        "implementation": {
            "type": "template",
            "template_action": {
                "type": "action",
                "definition": {
                    "name": "triage",
                    "namespace": "acme",
                    "title": "Triage",
                    "display_group": "Acme",
                    "expects": {},
                    "steps": [
                        {"ref": "enrich", "action": "acme.enrich_ip", "args": {}}
                    ],
                    "returns": "${{ steps.enrich.result }}",
                },
            },
        },
    }
    version = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=template_repo.id,
        version="3.0.0",
        commit_sha="e" * 40,
        manifest={"actions": {"acme.triage": template}},
        tarball_uri="s3://registry/templates-3.0.0.tar.gz",
    )
    session.add(version)
    await session.flush()
    template_repo.current_version_id = version.id
    session.add(
        RegistryIndex(
            organization_id=svc_role.organization_id,
            registry_version_id=version.id,
            namespace="acme",
            name="triage",
            action_type="template",
            description="Triage an alert",
            options={"include_in_schema": True},
        )
    )
    await session.flush()

    requirements = await package_registry_requirements(
        source.service,
        _spec_using("acme.triage"),
        organization_id=source.service.organization_id,
    )

    assert {(r.origin, tuple(r.actions)) for r in requirements} == {
        (template_repo.origin, ("acme.triage",)),
        (step_repo.origin, ("acme.enrich_ip",)),
    }


def test_skill_declared_registry_tools_count_as_packaged_actions() -> None:
    skill = SkillResourceSpec(
        id="enricher",
        slug="enricher",
        name="enricher",
        file_contents={
            "SKILL.md": (
                "---\nname: enricher\nmetadata:\n  tools:\n"
                "    - acme.lookup_hash\n    - mcp.github\n---\n# Enricher\n"
            )
        },
    )

    names = packaged_action_names(WorkspaceSpec(skills={"enricher": skill}))

    # MCP tools are not registry actions, so only the registry tool counts.
    assert names == {"acme.lookup_hash"}


@pytest.mark.anyio
async def test_a_changed_source_binding_fails_closed(
    session: AsyncSession, svc_role: Role, source: SourceHarness
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_repo_url": "git+ssh://git@github.com/elsewhere/repo.git",
    }
    await session.flush()

    with pytest.raises(TracecatValidationError, match="Git binding changed"):
        await source.service.is_package_source()


def test_package_manifest_is_omitted_from_ordinary_manifests() -> None:
    assert "package" not in json.loads(canonical_json_text(WorkspaceManifest()))
