"""The cross-org rollout engine and its admin API (ENG-2297 to ENG-2303, ENG-2317).

A source workspace in one organization is seeded from the expanded sync
fixture and publishes package tags to a fake repository. Target workspaces
live in other organizations. Rollout phases run inline instead of on
Temporal, through the same engine the background activity calls.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.managed_resources.schemas import (
    ManagedReferenceMatchesUpdate,
    ManagedReferenceMatchItem,
    ManagedReleaseCreate,
    ManagedRolloutApplyRequest,
    ManagedRolloutPreviewRequest,
    ManagedTargetCreate,
)
from tracecat_ee.admin.managed_resources.service import AdminManagedResourcesService

from tests.support.fake_vcs import FakeVcsServer
from tests.support.managed_resources import (
    GIT_URL,
    REPO_URL,
    no_registry_lock,
    seed_workspace,
)
from tracecat.agent.preset.schemas import AgentPresetSkillBindingBase, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.skill.schemas import SkillCreate
from tracecat.agent.skill.service import SkillService
from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import (
    AgentPreset,
    AgentPresetSkill,
    ManagedDetachedCopy,
    ManagedResource,
    ManagedRollout,
    ManagedTarget,
    Organization,
    PlatformSetting,
    RegistryRepository,
    RegistryVersion,
    Table,
    User,
    Workflow,
    Workspace,
)
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.detach import ManagedDetachService
from tracecat.managed_resources.enums import (
    ManagedReferenceKind,
    ManagedRolloutKind,
    ManagedRolloutStatus,
)
from tracecat.managed_resources.guard import ManagedResourceLockedError
from tracecat.managed_resources.rollout import (
    ManagedRolloutEngine,
    _conflicts,
    _unused_match_keys,
    target_role,
)
from tracecat.managed_resources.source import ManagedSource, require_managed_source
from tracecat.managed_resources.source_setting import (
    MANAGED_RESOURCES_SOURCE_KEY,
    ManagedSourceSetting,
)
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.sync import PullDiagnostic
from tracecat.tables.schemas import TableRowInsert
from tracecat.tables.service import TablesService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowCreate, WorkflowUpdate
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    CaseDropdownResourceSpec,
    CaseFieldResourceSpec,
    PackageRegistryRequirement,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportRequest,
)
from tracecat.workspace_sync.service import WorkspaceSyncService

pytestmark = pytest.mark.usefixtures("db")

V1 = [
    "workflows/qa-child/**",
    "agent_presets/qa-triage-parent/**",
    "tables/qa_indicators/**",
]
# v2 drops the table, adds the orphan workflow, and changes the preset.
V2 = [
    "workflows/qa-child/**",
    "agent_presets/qa-triage-parent/**",
    "workflows/qa-orphan/**",
]


class InlineRunner:
    """Run rollout phases immediately on the test session."""

    def __init__(self, session: AsyncSession, fake_vcs: FakeVcsServer) -> None:
        self._engine = ManagedRolloutEngine(
            session, transport_factory=fake_vcs.transport_factory
        )

    async def start(
        self, rollout_id: uuid.UUID, phase: Literal["preview", "apply"]
    ) -> None:
        if phase == "preview":
            await self._engine.preview(rollout_id)
        else:
            await self._engine.apply(rollout_id)


@dataclass(frozen=True, slots=True)
class Harness:
    session: AsyncSession
    source: WorkspaceSyncService
    source_role: Role
    fake_vcs: FakeVcsServer
    admin: AdminManagedResourcesService

    async def publish(self, tag: str, patterns: list[str]) -> None:
        await self.source.export_workspace(
            WorkspaceSyncExportRequest(
                message=f"Package {tag}", branch="main", package_include=patterns
            )
        )
        self.fake_vcs.create_tag(GIT_URL, tag=tag, ref="main")

    async def new_target(self, name: str) -> Workspace:
        org = Organization(
            name=f"Customer {name}", slug=f"cust-{uuid.uuid4().hex[:8]}", is_active=True
        )
        self.session.add(org)
        await self.session.flush()
        workspace = Workspace(name=name, organization_id=org.id)
        self.session.add(workspace)
        await self.session.commit()
        return workspace

    async def assign(self, workspace: Workspace, tag: str) -> uuid.UUID:
        target = await self.admin.assign_target(
            ManagedTargetCreate(workspace_id=workspace.id, pinned_tag=tag)
        )
        return target.id

    async def roll(
        self, target_ids: list[uuid.UUID], tag: str
    ) -> list[ManagedRolloutStatus]:
        """Preview ``tag`` for the targets, apply what is ready, return statuses."""
        previews = await self.admin.preview_rollouts(
            ManagedRolloutPreviewRequest(target_ids=target_ids, tag=tag)
        )
        applied = await self.admin.apply_rollouts(
            ManagedRolloutApplyRequest(rollout_ids=[p.id for p in previews])
        )
        return [rollout.status for rollout in applied]


@pytest.fixture
async def harness(session: AsyncSession, svc_role: Role) -> AsyncIterator[Harness]:
    fake_vcs = FakeVcsServer()
    source = await seed_workspace(session, svc_role, fake_vcs=fake_vcs)
    assert svc_role.workspace_id is not None
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
    staff = User(
        id=uuid.uuid4(),
        email=f"fde-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    session.add(staff)
    await session.commit()
    admin = AdminManagedResourcesService(
        session,
        PlatformRole(type="user", user_id=staff.id, service_id="tracecat-api"),
        runner=InlineRunner(session, fake_vcs),
        transport_factory=fake_vcs.transport_factory,
    )
    with no_registry_lock():
        harness = Harness(session, source, svc_role, fake_vcs, admin)
        await harness.publish("v1.0.0", V1)
        yield harness


async def _managed(session: AsyncSession, workspace: Workspace) -> set[tuple[str, str]]:
    rows = await session.scalars(
        select(ManagedResource).where(ManagedResource.workspace_id == workspace.id)
    )
    return {(row.resource_type, row.source_id) for row in rows}


async def _workflow(
    session: AsyncSession, workspace: Workspace, alias: str
) -> Workflow | None:
    return await session.scalar(
        select(Workflow).where(
            Workflow.workspace_id == workspace.id, Workflow.alias == alias
        )
    )


@pytest.mark.anyio
async def test_first_rollout_creates_locked_managed_resources(harness: Harness) -> None:
    customer = await harness.new_target("alpha")
    target_id = await harness.assign(customer, "v1.0.0")

    assert await harness.roll([target_id], "v1.0.0") == [ManagedRolloutStatus.APPLIED]

    managed = await _managed(harness.session, customer)
    assert ("workflow", "qa-child") in managed
    assert ("agent_preset", "qa-triage-parent") in managed
    assert ("table", "qa_indicators") in managed
    child = await _workflow(harness.session, customer, "qa-child")
    assert child is not None
    with pytest.raises(ManagedResourceLockedError):
        await WorkflowsManagementService(
            harness.session, role=target_role(customer)
        ).update_workflow(
            WorkflowUUID.new(child.id), WorkflowUpdate(title="Customer edit")
        )
    target = await harness.session.scalar(
        select(ManagedTarget).where(ManagedTarget.id == target_id)
    )
    assert target is not None
    assert target.last_applied_tag == "v1.0.0"
    assert target.last_rollout_status == "up_to_date"


@pytest.mark.anyio
async def test_rolling_the_same_tag_again_has_no_changes(harness: Harness) -> None:
    customer = await harness.new_target("steady")
    target_id = await harness.assign(customer, "v1.0.0")
    await harness.roll([target_id], "v1.0.0")

    previews = await harness.admin.preview_rollouts(
        ManagedRolloutPreviewRequest(target_ids=[target_id])
    )

    assert previews[0].status == ManagedRolloutStatus.NO_CHANGES


async def _add_customer_skill(session: AsyncSession, workspace: Workspace) -> uuid.UUID:
    role = target_role(workspace)
    skills = SkillService(session, role=role)
    skill = await skills.create_skill(SkillCreate(name="customer-runbook"))
    await skills.publish_skill(skill.id)
    preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == workspace.id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert preset is not None
    current = list(
        await session.scalars(
            select(AgentPresetSkill.skill_id).where(
                AgentPresetSkill.preset_id == preset.id
            )
        )
    )
    await AgentPresetService(session, role=role).update_preset(
        preset,
        AgentPresetUpdate(
            skills=[
                AgentPresetSkillBindingBase(skill_id=skill_id)
                for skill_id in [*current, skill.id]
            ]
        ),
    )
    return skill.id


async def _change_source_preset(harness: Harness) -> None:
    preset = await harness.session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == harness.source_role.workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert preset is not None
    await AgentPresetService(harness.session, role=harness.source_role).update_preset(
        preset, AgentPresetUpdate(instructions="Version two instructions")
    )


@pytest.mark.anyio
async def test_bump_updates_adds_deletes_and_keeps_customer_state(
    harness: Harness,
) -> None:
    customer = await harness.new_target("bravo")
    target_id = await harness.assign(customer, "v1.0.0")
    await harness.roll([target_id], "v1.0.0")
    customer_skill = await _add_customer_skill(harness.session, customer)
    table = await harness.session.scalar(
        select(Table).where(
            Table.workspace_id == customer.id, Table.name == "qa_indicators"
        )
    )
    assert table is not None
    await TablesService(harness.session, role=target_role(customer)).insert_row(
        table, TableRowInsert(data={"indicator": "203.0.113.9"})
    )
    await _change_source_preset(harness)
    await harness.publish("v2.0.0", V2)

    previews = await harness.admin.preview_rollouts(
        ManagedRolloutPreviewRequest(target_ids=[target_id], tag="v2.0.0")
    )
    preview = previews[0].preview
    assert preview is not None
    changes = {(c.change, c.resource_type, c.source_id) for c in preview.changes}
    assert ("added", "workflow", "qa-orphan") in changes
    assert ("modified", "agent_preset", "qa-triage-parent") in changes
    assert ("deleted", "table", "qa_indicators") in changes
    [table_warning] = [w for w in preview.delete_warnings if w.resource_type == "table"]
    assert table_warning.table_rows == 1

    applied = await harness.admin.apply_rollouts(
        ManagedRolloutApplyRequest(rollout_ids=[previews[0].id])
    )

    assert applied[0].status == ManagedRolloutStatus.APPLIED, applied[0].error
    assert await _workflow(harness.session, customer, "qa-orphan") is not None
    assert (
        await harness.session.scalar(
            select(Table).where(
                Table.workspace_id == customer.id, Table.name == "qa_indicators"
            )
        )
        is None
    )
    preset = await harness.session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == customer.id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert preset is not None
    await harness.session.refresh(preset)
    assert preset.instructions == "Version two instructions"
    bound = set(
        await harness.session.scalars(
            select(AgentPresetSkill.skill_id).where(
                AgentPresetSkill.preset_id == preset.id
            )
        )
    )
    assert customer_skill in bound
    target = await harness.session.scalar(
        select(ManagedTarget).where(ManagedTarget.id == target_id)
    )
    assert target is not None
    assert target.pinned_tag == "v2.0.0"


@pytest.mark.anyio
async def test_conflict_blocks_one_target_and_the_other_proceeds(
    harness: Harness,
) -> None:
    clean = await harness.new_target("charlie")
    clashing = await harness.new_target("delta")
    clean_id = await harness.assign(clean, "v1.0.0")
    clashing_id = await harness.assign(clashing, "v1.0.0")
    await harness.roll([clean_id, clashing_id], "v1.0.0")
    management = WorkflowsManagementService(harness.session, role=target_role(clashing))
    own = await management.create_workflow(WorkflowCreate(title="Customer orphan"))
    await management.update_workflow(own.id, WorkflowUpdate(alias="qa-orphan"))
    await harness.publish("v2.0.0", V2)

    previews = await harness.admin.preview_rollouts(
        ManagedRolloutPreviewRequest(target_ids=[clean_id, clashing_id], tag="v2.0.0")
    )
    by_target = {p.target_id: p for p in previews}
    blocked = by_target[clashing_id]
    assert blocked.status == ManagedRolloutStatus.BLOCKED
    assert blocked.preview is not None
    assert [(c.resource_type, c.name) for c in blocked.preview.conflicts] == [
        ("workflow", "qa-orphan")
    ]
    applied = await harness.admin.apply_rollouts(
        ManagedRolloutApplyRequest(rollout_ids=[p.id for p in previews])
    )

    statuses = {rollout.target_id: rollout.status for rollout in applied}
    assert statuses[clean_id] == ManagedRolloutStatus.APPLIED
    assert statuses[clashing_id] == ManagedRolloutStatus.BLOCKED
    assert ("workflow", "qa-orphan") not in await _managed(harness.session, clashing)
    await harness.session.refresh(own)
    assert own.title == "Customer orphan"


@pytest.mark.anyio
async def test_pinning_an_older_tag_rolls_back(harness: Harness) -> None:
    customer = await harness.new_target("echo")
    target_id = await harness.assign(customer, "v1.0.0")
    await harness.roll([target_id], "v1.0.0")
    await harness.publish("v2.0.0", V2)
    await harness.roll([target_id], "v2.0.0")

    assert await harness.roll([target_id], "v1.0.0") == [ManagedRolloutStatus.APPLIED]

    managed = await _managed(harness.session, customer)
    assert ("workflow", "qa-orphan") not in managed
    assert ("table", "qa_indicators") in managed
    assert await _workflow(harness.session, customer, "qa-orphan") is None


@pytest.mark.anyio
async def test_apply_refuses_a_preview_the_target_has_moved_past(
    harness: Harness,
) -> None:
    customer = await harness.new_target("foxtrot")
    target_id = await harness.assign(customer, "v1.0.0")
    [preview] = await harness.admin.preview_rollouts(
        ManagedRolloutPreviewRequest(target_ids=[target_id])
    )
    # The stale path rolls the session back, expiring loaded objects.
    customer_id = customer.id
    # After the preview, the customer takes a name the tag brings in.
    management = WorkflowsManagementService(harness.session, role=target_role(customer))
    own = await management.create_workflow(
        WorkflowCreate(title="Created after preview")
    )
    await management.update_workflow(own.id, WorkflowUpdate(alias="qa-child"))

    [applied] = await harness.admin.apply_rollouts(
        ManagedRolloutApplyRequest(rollout_ids=[preview.id])
    )

    assert applied.status == ManagedRolloutStatus.STALE
    assert (
        await harness.session.scalar(
            select(ManagedResource).where(ManagedResource.workspace_id == customer_id)
        )
        is None
    )


@pytest.mark.anyio
async def test_unassign_deletes_only_managed_resources(harness: Harness) -> None:
    customer = await harness.new_target("golf")
    target_id = await harness.assign(customer, "v1.0.0")
    await harness.roll([target_id], "v1.0.0")
    child = await _workflow(harness.session, customer, "qa-child")
    assert child is not None
    copy = await ManagedDetachService(
        harness.session, role=target_role(customer)
    ).detach(
        resource_type=SyncResourceType.WORKFLOW,
        local_id=child.id,
        new_name="qa-child-mine",
    )
    management = WorkflowsManagementService(harness.session, role=target_role(customer))
    own = await management.create_workflow(WorkflowCreate(title="Customer own"))

    unassign = await harness.admin.unassign_target(target_id)
    assert unassign.status == ManagedRolloutStatus.READY
    assert unassign.preview is not None
    assert {c.change for c in unassign.preview.changes} == {"deleted"}
    [applied] = await harness.admin.apply_rollouts(
        ManagedRolloutApplyRequest(rollout_ids=[unassign.id])
    )

    assert applied.status == ManagedRolloutStatus.APPLIED, applied.error
    assert await _managed(harness.session, customer) == set()
    assert await _workflow(harness.session, customer, "qa-child") is None
    assert (
        await harness.session.scalar(
            select(Workflow).where(Workflow.id == copy.local_id)
        )
        is not None
    )
    assert (
        await harness.session.scalar(select(Workflow).where(Workflow.id == own.id))
        is not None
    )
    assert (
        await harness.session.scalar(
            select(ManagedTarget).where(ManagedTarget.id == target_id)
        )
        is None
    )
    assert (
        await harness.session.scalar(
            select(ManagedDetachedCopy).where(
                ManagedDetachedCopy.local_id == copy.local_id
            )
        )
        is not None
    )


@pytest.mark.anyio
async def test_assign_refuses_the_source_and_unknown_tags(harness: Harness) -> None:
    source_workspace = await harness.session.scalar(
        select(Workspace).where(Workspace.id == harness.source_role.workspace_id)
    )
    assert source_workspace is not None
    customer = await harness.new_target("hotel")

    with pytest.raises(TracecatValidationError, match="source workspace"):
        await harness.assign(source_workspace, "v1.0.0")
    with pytest.raises(TracecatNotFoundError, match="v9.9.9"):
        await harness.assign(customer, "v9.9.9")


@pytest.mark.anyio
async def test_cut_release_tags_the_base_branch_head(harness: Harness) -> None:
    release = await harness.admin.cut_release(ManagedReleaseCreate(tag="v1.1.0"))

    head = harness.fake_vcs.repo_files(GIT_URL, ref="main")
    tagged = harness.fake_vcs.repo_files(GIT_URL, ref=release.commit_sha)
    assert tagged == head
    with pytest.raises(TracecatValidationError, match="already exists"):
        await harness.admin.cut_release(ManagedReleaseCreate(tag="v1.1.0"))
    with pytest.raises(TracecatValidationError, match="v1.2.3"):
        await harness.admin.cut_release(ManagedReleaseCreate(tag="release-1"))


@pytest.mark.anyio
async def test_cut_release_refuses_a_branch_that_moved_past_the_reviewed_commit(
    harness: Harness,
) -> None:
    reviewed = await harness.admin.release_candidate()
    await harness.publish("v1.1.0", V2)

    with pytest.raises(TracecatValidationError, match="moved"):
        await harness.admin.cut_release(
            ManagedReleaseCreate(tag="v1.2.0", commit_sha=reviewed.commit_sha)
        )
    current = await harness.admin.release_candidate()
    release = await harness.admin.cut_release(
        ManagedReleaseCreate(tag="v1.2.0", commit_sha=current.commit_sha)
    )
    assert release.commit_sha == current.commit_sha


@pytest.mark.anyio
async def test_saved_reference_matches_are_listed_and_replaced(
    harness: Harness,
) -> None:
    customer = await harness.new_target("india")
    target_id = await harness.assign(customer, "v1.0.0")
    store_id = uuid.uuid4()

    await harness.admin.save_references(
        target_id,
        ManagedReferenceMatchesUpdate(
            matches=[
                ManagedReferenceMatchItem(
                    reference_kind=ManagedReferenceKind.SECRET_STORE,
                    source_key="source-store",
                    resolved_id=store_id,
                )
            ]
        ),
    )
    saved = await harness.admin.save_references(
        target_id,
        ManagedReferenceMatchesUpdate(
            matches=[
                ManagedReferenceMatchItem(
                    reference_kind=ManagedReferenceKind.SECRET_STORE,
                    source_key="source-store",
                    resolved_id=None,
                )
            ]
        ),
    )

    assert [(m.source_key, m.resolved_id) for m in saved] == [("source-store", None)]

    # The request replaces the set: an omitted match stops applying.
    assert (
        await harness.admin.save_references(
            target_id, ManagedReferenceMatchesUpdate(matches=[])
        )
        == []
    )
    with pytest.raises(TracecatValidationError, match="UUID"):
        await harness.admin.save_references(
            target_id,
            ManagedReferenceMatchesUpdate(
                matches=[
                    ManagedReferenceMatchItem(
                        reference_kind=ManagedReferenceKind.MODEL,
                        source_key="not-a-uuid",
                        resolved_id=uuid.uuid4(),
                    )
                ]
            ),
        )


async def _source_registry_version(
    session: AsyncSession, organization_id: uuid.UUID, origin: str
) -> RegistryVersion:
    repo = RegistryRepository(organization_id=organization_id, origin=origin)
    session.add(repo)
    await session.flush()
    version = RegistryVersion(
        organization_id=organization_id,
        repository_id=repo.id,
        version="1.2.0",
        commit_sha="b" * 40,
        manifest={"actions": {}},
        tarball_uri="s3://registry/acme-1.2.0.tar.gz",
    )
    session.add(version)
    await session.flush()
    repo.current_version_id = version.id
    await session.commit()
    return version


@pytest.mark.anyio
async def test_registry_version_is_copied_into_the_target_org(
    harness: Harness,
) -> None:
    origin = f"git+ssh://git@github.com/acme/registry-{uuid.uuid4().hex[:6]}.git"
    assert harness.source_role.organization_id is not None
    await _source_registry_version(
        harness.session, harness.source_role.organization_id, origin
    )
    customer = await harness.new_target("juliet")
    requirement = PackageRegistryRequirement(
        origin=origin, version="1.2.0", commit_sha="b" * 40, actions=["acme.enrich_ip"]
    )
    engine = ManagedRolloutEngine(
        harness.session, transport_factory=harness.fake_vcs.transport_factory
    )
    managed_source = await _managed_source(harness)

    [before] = await engine._registry_plan(customer, managed_source, [requirement])
    await engine._install_registry(customer, managed_source, [requirement], None)
    await harness.session.commit()
    [after] = await engine._registry_plan(customer, managed_source, [requirement])

    assert before.action == "install"
    assert after.action == "present"
    repo = await harness.session.scalar(
        select(RegistryRepository).where(
            RegistryRepository.organization_id == customer.organization_id,
            RegistryRepository.origin == origin,
        )
    )
    assert repo is not None
    copied = await harness.session.scalar(
        select(RegistryVersion).where(RegistryVersion.id == repo.current_version_id)
    )
    assert copied is not None
    assert copied.tarball_uri == "s3://registry/acme-1.2.0.tar.gz"


@pytest.mark.anyio
async def test_customer_registry_repo_with_the_same_origin_blocks(
    harness: Harness,
) -> None:
    origin = f"git+ssh://git@github.com/acme/registry-{uuid.uuid4().hex[:6]}.git"
    assert harness.source_role.organization_id is not None
    await _source_registry_version(
        harness.session, harness.source_role.organization_id, origin
    )
    customer = await harness.new_target("kilo")
    harness.session.add(
        RegistryRepository(organization_id=customer.organization_id, origin=origin)
    )
    await harness.session.commit()
    engine = ManagedRolloutEngine(harness.session)

    [plan] = await engine._registry_plan(
        customer,
        await _managed_source(harness),
        [PackageRegistryRequirement(origin=origin, version="1.2.0")],
    )

    assert plan.action == "blocked"
    assert plan.reason is not None and "its own registry" in plan.reason


async def _managed_source(harness: Harness) -> ManagedSource:
    return await require_managed_source(harness.session)


@pytest.mark.anyio
async def test_a_package_installed_registry_repo_is_locked_in_the_target_org(
    harness: Harness,
) -> None:
    origin = f"git+ssh://git@github.com/acme/registry-{uuid.uuid4().hex[:6]}.git"
    assert harness.source_role.organization_id is not None
    await _source_registry_version(
        harness.session, harness.source_role.organization_id, origin
    )
    customer = await harness.new_target("lima")
    engine = ManagedRolloutEngine(harness.session)
    await engine._install_registry(
        customer,
        await _managed_source(harness),
        [PackageRegistryRequirement(origin=origin, version="1.2.0")],
        None,
    )
    await harness.session.commit()
    repo = await harness.session.scalar(
        select(RegistryRepository).where(
            RegistryRepository.organization_id == customer.organization_id,
            RegistryRepository.origin == origin,
        )
    )
    assert repo is not None
    org_role = Role(
        type="service",
        service_id="tracecat-service",
        organization_id=customer.organization_id,
        scopes=frozenset({"*"}),
    )

    with pytest.raises(ManagedResourceLockedError):
        await RegistryReposService(harness.session, org_role).delete_repository(repo)


@pytest.mark.anyio
async def test_a_registry_the_tag_no_longer_needs_is_released(
    harness: Harness,
) -> None:
    origin = f"git+ssh://git@github.com/acme/registry-{uuid.uuid4().hex[:6]}.git"
    assert harness.source_role.organization_id is not None
    await _source_registry_version(
        harness.session, harness.source_role.organization_id, origin
    )
    customer = await harness.new_target("mike")
    engine = ManagedRolloutEngine(harness.session)
    await engine._install_registry(
        customer,
        await _managed_source(harness),
        [PackageRegistryRequirement(origin=origin, version="1.2.0")],
        None,
    )
    await harness.session.flush()

    # The next tag ships no custom actions.
    await engine._drop_unneeded_registry(
        customer, await engine._managed_rows(customer.id), []
    )
    await harness.session.commit()

    assert await engine._managed_rows(customer.id) == []
    assert (
        await harness.session.scalar(
            select(RegistryRepository).where(
                RegistryRepository.organization_id == customer.organization_id,
                RegistryRepository.origin == origin,
            )
        )
        is None
    )


@pytest.mark.anyio
async def test_a_superseded_preview_is_not_revived(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A newer preview marks this one STALE while it is still being computed;
    # the finishing worker must leave it STALE with no preview.
    customer = await harness.new_target("november")
    target_id = await harness.assign(customer, "v1.0.0")
    rollout = ManagedRollout(
        workspace_id=customer.id,
        target_id=target_id,
        kind=ManagedRolloutKind.APPLY,
        status=ManagedRolloutStatus.PREVIEWING,
        tag="v1.0.0",
    )
    harness.session.add(rollout)
    await harness.session.commit()
    rollout_id = rollout.id
    plan_apply = ManagedRolloutEngine._plan_apply

    async def superseded_mid_plan(engine: ManagedRolloutEngine, row: ManagedRollout):
        plan = await plan_apply(engine, row)
        # What the admin service does when a newer preview starts.
        await engine.session.execute(
            update(ManagedRollout)
            .where(ManagedRollout.id == rollout_id)
            .values(status=ManagedRolloutStatus.STALE)
        )
        return plan

    monkeypatch.setattr(ManagedRolloutEngine, "_plan_apply", superseded_mid_plan)

    finished = await ManagedRolloutEngine(
        harness.session, transport_factory=harness.fake_vcs.transport_factory
    ).preview(rollout_id)

    assert finished.status == ManagedRolloutStatus.STALE
    assert finished.preview is None


@pytest.mark.anyio
async def test_a_failed_delete_does_not_stop_the_others(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The failed delete rolls back, which expires every loaded row; the loop
    # must still report it and delete the rest.
    customer = await harness.new_target("oscar")
    rows = [
        ManagedResource(
            workspace_id=customer.id,
            resource_type=SyncResourceType.VARIABLE.value,
            local_id=uuid.uuid4(),
            source_id=f"default/var_{name}",
            source_path=f"variables/default/var_{name}.yml",
            deployed_tag="v1.0.0",
            deployed_commit_sha="a" * 40,
        )
        for name in ("broken", "fine")
    ]
    harness.session.add_all(rows)
    await harness.session.commit()
    broken_local_id = rows[0].local_id

    async def delete_resource(session, role, row) -> None:
        if row.local_id == broken_local_id:
            raise RuntimeError("delete failed")

    monkeypatch.setattr(
        "tracecat.managed_resources.rollout._delete_resource", delete_resource
    )
    engine = ManagedRolloutEngine(harness.session)

    failures = await engine._delete_managed(customer, rows)

    assert failures == ["variable default/var_broken"]
    remaining = await engine._managed_rows(customer.id)
    assert [row.source_id for row in remaining] == ["default/var_broken"]


def test_matches_a_tag_no_longer_uses_are_recognised_from_diagnostics() -> None:
    # A later tag can drop the last model, MCP integration or store a saved
    # match points at; the preview retries without those matches.
    def diagnostic(**details: str) -> PullDiagnostic:
        return PullDiagnostic(
            workflow_path="",
            workflow_title=None,
            error_type="validation",
            message="unused",
            details=details,
        )

    catalog_id = str(uuid.uuid4())
    mcp_id = str(uuid.uuid4())
    unused = _unused_match_keys(
        [
            diagnostic(code="catalog_mapping_source_not_found", catalog_id=catalog_id),
            diagnostic(
                code="mcp_integration_mapping_source_not_found",
                mcp_integration_id=mcp_id,
            ),
            diagnostic(code="secret_store_mapping_source_not_found", store="old"),
            diagnostic(code="something_else", store="ignored"),
        ]
    )

    assert unused == {"catalog": {catalog_id}, "mcp": {mcp_id}, "stores": {"old"}}


def test_a_customer_dropdown_with_the_incoming_ref_is_a_conflict() -> None:
    # The importer adopts a dropdown whose ref equals the source id, even
    # under a different name, so that blocks the rollout too.
    existing = uuid.uuid4()
    spec = WorkspaceSpec(
        case_dropdowns={
            "severity": CaseDropdownResourceSpec.model_construct(
                version=1, type="case_dropdown", id="severity", name="Severity"
            )
        }
    )

    [conflict] = _conflicts(
        WorkspaceRemoteSnapshot(commit_sha="", files={}, spec=spec),
        {("case_dropdown", ("ref", "severity")): existing},
        [],
    )

    assert (conflict.source_id, conflict.existing_local_id) == ("severity", existing)


def test_a_customer_case_field_named_like_the_source_id_is_a_conflict() -> None:
    # The importer would adopt the customer's "sev_src" field by source id and
    # rename it, so it must block like a name clash does.
    existing = uuid.uuid4()
    spec = WorkspaceSpec(
        case_fields={
            "sev_src": CaseFieldResourceSpec.model_construct(
                version=1, type="case_field", id="sev_src", name="severity"
            )
        }
    )

    [conflict] = _conflicts(
        WorkspaceRemoteSnapshot(commit_sha="", files={}, spec=spec),
        {("case_field", ("sev_src",)): existing},
        [],
    )

    assert (conflict.source_id, conflict.name, conflict.existing_local_id) == (
        "sev_src",
        "sev_src",
        existing,
    )


@pytest.mark.anyio
async def test_release_candidate_shows_the_head_commit_and_its_package(
    harness: Harness,
) -> None:
    before = await harness.admin.release_candidate()

    assert before.branch == "main"
    assert before.existing_tags == ["v1.0.0"]
    assert before.package is not None
    assert {(e.resource_type, e.source_id) for e in before.package.explicit} == {
        ("workflow", "qa-child"),
        ("agent_preset", "qa-triage-parent"),
        ("table", "qa_indicators"),
    }

    await harness.publish("v1.1.0", V2)
    after = await harness.admin.release_candidate()

    assert after.commit_sha != before.commit_sha
    assert after.existing_tags == ["v1.1.0"]
    assert after.package is not None
    explicit = {(e.resource_type, e.source_id) for e in after.package.explicit}
    assert ("workflow", "qa-orphan") in explicit
    assert ("table", "qa_indicators") not in explicit
