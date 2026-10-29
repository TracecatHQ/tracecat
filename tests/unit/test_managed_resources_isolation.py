"""A target workspace's own Git sync and detach (ENG-2294, ENG-2296)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer
from tests.support.managed_resources import (
    GIT_URL,
    local_id_of,
    mark_managed,
    no_registry_lock,
    seed_workspace,
)
from tests.unit.test_workspace_sync_acceptance_contract import _workflow_spec, _yaml
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentPreset,
    CaseDropdownDefinition,
    ManagedDetachedCopy,
    Skill,
    Workflow,
    Workspace,
    WorkspaceSyncResourceMapping,
)
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.detach import ManagedDetachService
from tracecat.managed_resources.guard import ManagedResourceLockedError
from tracecat.sync import PullOptions
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    WorkspaceManifest,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportRequest,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def target(
    session: AsyncSession, svc_role: Role
) -> AsyncIterator[tuple[WorkspaceSyncService, FakeVcsServer]]:
    fake_vcs = FakeVcsServer()
    service = await seed_workspace(session, svc_role, fake_vcs=fake_vcs)
    child = WorkflowUUID.new(
        await local_id_of(session, Workflow, svc_role, alias="qa-child")
    )
    await mark_managed(
        session, svc_role, SyncResourceType.WORKFLOW, child, source_id="qa-child"
    )
    with no_registry_lock():
        yield service, fake_vcs


@pytest.mark.anyio
async def test_customer_push_leaves_out_managed_resources_but_keeps_references(
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    service, fake_vcs = target

    result = await service.export_workspace(
        WorkspaceSyncExportRequest(message="Customer push", branch="customer/push")
    )

    assert result.commit.sha is not None
    files = fake_vcs.repo_files(GIT_URL, ref=result.commit.sha)
    workflow_files = {path for path in files if path.startswith("workflows/")}
    assert "workflows/qa-child/definition.yml" not in workflow_files
    assert "workflows/qa-root/definition.yml" in workflow_files
    # The customer workflow still calls the managed child by alias.
    assert "qa-child" in files["workflows/qa-root/definition.yml"]


@pytest.mark.anyio
async def test_customer_pull_skips_a_workflow_that_collides_with_a_managed_alias(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    service, fake_vcs = target
    child = await session.scalar(
        select(Workflow).where(
            Workflow.workspace_id == svc_role.workspace_id, Workflow.alias == "qa-child"
        )
    )
    assert child is not None
    title_before = child.title
    commit = await fake_vcs.transport_factory(
        VcsProvider.GITHUB, session=session, role=svc_role
    ).write_files(
        url=GIT_URL,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "workflows/customer-child/definition.yml": _yaml(
                _workflow_spec(
                    source_id="customer-child",
                    title="Customer overwrite attempt",
                    alias="qa-child",
                    folder_path="QA/Children",
                    actions=[
                        {
                            "ref": "noop",
                            "action": "core.transform.reshape",
                            "args": {"value": 1},
                        }
                    ],
                )
            ),
        },
        message="Customer repo",
        branch="customer/main",
        create_pr=False,
    )
    assert commit.sha is not None

    preview = await service.pull(
        options=PullOptions(commit_sha=commit.sha, dry_run=True)
    )
    applied = await service.pull(options=PullOptions(commit_sha=commit.sha))

    assert preview.skipped_managed == ["workflows/customer-child/definition.yml"]
    assert applied.success, applied.diagnostics
    assert applied.skipped_managed == ["workflows/customer-child/definition.yml"]
    await session.refresh(child)
    assert child.title == title_before


@pytest.mark.anyio
async def test_customer_pull_skips_a_dropdown_adopted_by_a_managed_ref(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    # The importer adopts a dropdown by ref, even under another name, so a
    # customer file whose source id is a managed dropdown's ref must skip.
    service, fake_vcs = target
    dropdown = await session.scalar(
        select(CaseDropdownDefinition).where(
            CaseDropdownDefinition.workspace_id == svc_role.workspace_id,
            CaseDropdownDefinition.ref == "qa_resolution_reason",
        )
    )
    assert dropdown is not None
    await mark_managed(
        session,
        svc_role,
        SyncResourceType.CASE_DROPDOWN,
        dropdown.id,
        source_id="qa_resolution_reason",
    )
    # A customer repository that never mapped the managed dropdown.
    await session.execute(
        delete(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
            WorkspaceSyncResourceMapping.local_id == dropdown.id,
        )
    )
    name_before = dropdown.name
    path = "case_dropdowns/qa_resolution_reason.yml"
    commit = await fake_vcs.transport_factory(
        VcsProvider.GITHUB, session=session, role=svc_role
    ).write_files(
        url=GIT_URL,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            path: _yaml(
                {
                    "version": 1,
                    "type": "case_dropdown",
                    "id": "qa_resolution_reason",
                    "name": "Customer rename attempt",
                    "options": [{"label": "Only", "ref": "only"}],
                }
            ),
        },
        message="Customer repo",
        branch="customer/dropdown",
        create_pr=False,
    )
    assert commit.sha is not None

    applied = await service.pull(options=PullOptions(commit_sha=commit.sha))

    assert applied.success, applied.diagnostics
    assert applied.skipped_managed == [path]
    await session.refresh(dropdown)
    assert dropdown.name == name_before


@pytest.mark.anyio
async def test_customer_pull_checks_managed_resources_under_the_ownership_lock(
    target: tuple[WorkspaceSyncService, FakeVcsServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lock is held from the managed check through the import, so a
    # rollout cannot claim a resource in between.
    service, _ = target
    calls: list[bool] = []

    async def record(session, workspace_id, *, shared: bool) -> None:
        calls.append(shared)

    monkeypatch.setattr("tracecat.managed_resources.guard.hold_ownership_lock", record)

    await service._skip_managed_incoming(
        WorkspaceRemoteSnapshot(commit_sha="", files={}, spec=WorkspaceSpec())
    )

    assert calls == [True]


@pytest.mark.anyio
async def test_detach_makes_an_editable_copy_and_leaves_the_original_locked(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    child = WorkflowUUID.new(
        await local_id_of(session, Workflow, svc_role, alias="qa-child")
    )

    # The fixture role's user is not a row in ``user``; detach records the actor.
    admin = svc_role.model_copy(update={"user_id": None})
    copy = await ManagedDetachService(session, role=admin).detach(
        resource_type=SyncResourceType.WORKFLOW,
        local_id=child,
        new_name="qa-child-copy",
    )

    management = WorkflowsManagementService(session, role=svc_role)
    edited = await management.update_workflow(
        copy.local_id, WorkflowUpdate(title="Customer version")
    )
    assert edited.alias == "qa-child-copy"
    with pytest.raises(ManagedResourceLockedError):
        await management.update_workflow(child, WorkflowUpdate(title="Nope"))
    record = await session.scalar(
        select(ManagedDetachedCopy).where(ManagedDetachedCopy.local_id == copy.local_id)
    )
    assert record is not None
    assert record.original_local_id == child
    assert record.original_name == "qa-child-enrichment"
    assert record.original_source_id == "qa-child"


@pytest.mark.anyio
async def test_detach_copies_a_skill_under_its_new_name(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    # The importer requires SKILL.md's frontmatter name to match the spec, so
    # the copy's SKILL.md must carry the new name.
    skill_id = await local_id_of(session, Skill, svc_role, slug="qa-enrichment-skill")
    await mark_managed(
        session,
        svc_role,
        SyncResourceType.SKILL,
        skill_id,
        source_id="qa-enrichment-skill",
    )
    admin = svc_role.model_copy(update={"user_id": None})

    copy = await ManagedDetachService(session, role=admin).detach(
        resource_type=SyncResourceType.SKILL,
        local_id=skill_id,
        new_name="qa-enrichment-copy",
    )

    skill = await session.scalar(select(Skill).where(Skill.id == copy.local_id))
    assert skill is not None
    assert skill.slug == "qa-enrichment-copy"
    assert copy.original_local_id == skill_id


@pytest.mark.anyio
async def test_detaching_the_default_agent_keeps_it_the_default(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    preset_id = await local_id_of(
        session, AgentPreset, svc_role, slug="qa-triage-parent"
    )
    await mark_managed(
        session,
        svc_role,
        SyncResourceType.AGENT_PRESET,
        preset_id,
        source_id="qa-triage-parent",
    )
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.default_agent_preset_id = preset_id
    await session.flush()
    admin = svc_role.model_copy(update={"user_id": None})

    copy = await ManagedDetachService(session, role=admin).detach(
        resource_type=SyncResourceType.AGENT_PRESET,
        local_id=preset_id,
        new_name="qa-triage-copy",
    )

    await session.refresh(workspace)
    assert copy.local_id != preset_id
    assert workspace.default_agent_preset_id == preset_id


@pytest.mark.anyio
async def test_detach_refuses_a_name_another_workflow_uses(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    child = WorkflowUUID.new(
        await local_id_of(session, Workflow, svc_role, alias="qa-child")
    )

    with pytest.raises(TracecatConflictError):
        await ManagedDetachService(session, role=svc_role).detach(
            resource_type=SyncResourceType.WORKFLOW,
            local_id=child,
            new_name="qa-orphan",
        )


@pytest.mark.anyio
async def test_detach_refuses_an_unmanaged_resource(
    session: AsyncSession,
    svc_role: Role,
    target: tuple[WorkspaceSyncService, FakeVcsServer],
) -> None:
    orphan = WorkflowUUID.new(
        await local_id_of(session, Workflow, svc_role, alias="qa-orphan")
    )

    with pytest.raises(TracecatValidationError, match="Only managed"):
        await ManagedDetachService(session, role=svc_role).detach(
            resource_type=SyncResourceType.WORKFLOW,
            local_id=orphan,
            new_name=f"copy-{uuid.uuid4().hex[:4]}",
        )
