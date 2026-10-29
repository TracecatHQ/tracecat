"""The cross-org rollout engine: apply a package tag to one target workspace.

A rollout reads the source repository at a tag with the source org's
credentials and writes into a target workspace in another org. It reuses the
workspace sync parser, correlation and importer, with four differences from
an ordinary pull:

* **Matching** uses only the ``managed_resource`` table, through the
  dedicated ``managed`` mapping namespace. It never adopts by name.
* **Conflicts block.** An incoming resource whose name a customer resource
  already uses blocks the whole target. Nothing is written for it.
* **Deletes.** Managed resources absent from the tag are deleted.
* **Customer state survives:** skills customers added to managed presets,
  schedules, webhook and form on/off status, table rows and secret values.

Each rollout has two phases, recorded on a ``managed_rollout`` row:
``preview`` computes what would happen and stores it; ``apply`` re-checks
that the target still matches the preview and writes. Creates and updates
apply in one transaction; deletes run after it, one resource at a time,
because the resource services commit their own deletes. A failed delete
leaves its managed row, so the next rollout retries it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import orjson
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.agent.skill.service import SkillService
from tracecat.audit.enums import AuditEventStatus
from tracecat.auth.types import PlatformRole, Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.cases.dropdowns.service import CaseDropdownDefinitionsService
from tracecat.cases.durations.service import CaseDurationDefinitionService
from tracecat.cases.service import CaseFieldsService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import (
    AgentPreset,
    AgentPresetSkill,
    Case,
    CaseDropdownDefinition,
    CaseDropdownValue,
    CaseFields,
    CaseTag,
    CaseTagLink,
    Form,
    ManagedPresetSkillBinding,
    ManagedReferenceMatch,
    ManagedResource,
    ManagedRollout,
    ManagedTarget,
    RegistryRepository,
    RegistryVersion,
    Secret,
    Skill,
    Table,
    Webhook,
    Workspace,
    WorkspaceSyncResourceMapping,
    WorkspaceVariable,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.managed_resources.audit import audit_managed_event
from tracecat.managed_resources.enums import (
    ManagedReferenceKind,
    ManagedRolloutKind,
    ManagedRolloutStatus,
    ManagedTargetStatus,
)
from tracecat.managed_resources.guard import (
    REGISTRY_REPOSITORY_RESOURCE_TYPE,
    hold_ownership_lock,
    managed_write_bypass,
)
from tracecat.managed_resources.identities import (
    IdentityKey,
    spec_identity,
    workspace_identities,
)
from tracecat.managed_resources.presets import preserve_customer_skill_bindings
from tracecat.managed_resources.schemas import (
    RolloutChange,
    RolloutConflict,
    RolloutDeleteWarning,
    RolloutDroppedSkill,
    RolloutPreview,
    RolloutReference,
    RolloutRegistryPlan,
)
from tracecat.managed_resources.source import (
    ManagedSource,
    read_source_commit,
    require_managed_source,
    resolve_source_tag,
)
from tracecat.registry.versions.schemas import (
    RegistryVersionCreate,
    RegistryVersionManifest,
)
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.secrets.service import SecretsService
from tracecat.sync import PullResult
from tracecat.tables.service import TablesService
from tracecat.variables.service import VariablesService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workspace_sync.adapters import (
    WORKSPACE_RESOURCE_ADAPTERS,
    workspace_spec_from_maps,
)
from tracecat.workspace_sync.adapters.base import MANAGED_MAPPING_NAMESPACE
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    PackageRegistryRequirement,
    WorkflowResourceSpec,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    workspace_manifest_from_json,
)
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import VcsTransportFactory

REGISTRY_RESOURCE_TYPE = REGISTRY_REPOSITORY_RESOURCE_TYPE
"""``managed_resource.resource_type`` for a registry repository a package installed.

The row's ``source_id`` is the repository origin and ``source_path`` the
version this workspace's package needs. Registry repositories are org-level,
so one row per target workspace lets the engine see what every workspace in
the org needs.
"""

_TERMINAL_PREVIEW = frozenset(
    {
        ManagedRolloutStatus.READY,
        ManagedRolloutStatus.NO_CHANGES,
        ManagedRolloutStatus.BLOCKED,
        ManagedRolloutStatus.NEEDS_REFERENCES,
        ManagedRolloutStatus.FAILED,
    }
)

_AUDITED_OUTCOMES = frozenset(
    {
        ManagedRolloutStatus.APPLIED,
        ManagedRolloutStatus.BLOCKED,
        ManagedRolloutStatus.FAILED,
    }
)

_TARGET_STATUS_BY_ROLLOUT = {
    ManagedRolloutStatus.READY: ManagedTargetStatus.PENDING,
    ManagedRolloutStatus.NO_CHANGES: ManagedTargetStatus.UP_TO_DATE,
    ManagedRolloutStatus.BLOCKED: ManagedTargetStatus.BLOCKED,
    ManagedRolloutStatus.NEEDS_REFERENCES: ManagedTargetStatus.NEEDS_REFERENCES,
    ManagedRolloutStatus.FAILED: ManagedTargetStatus.FAILED,
    ManagedRolloutStatus.APPLIED: ManagedTargetStatus.UP_TO_DATE,
}


class StaleRolloutError(TracecatValidationError):
    """The target changed after its preview; preview again."""


@dataclass(frozen=True, slots=True)
class _Matches:
    """Saved reference matches, as pull mapping requests."""

    catalog: dict[uuid.UUID, uuid.UUID]
    mcp: dict[uuid.UUID, uuid.UUID]
    stores: dict[str, uuid.UUID | None]


@dataclass(frozen=True, slots=True)
class _Plan:
    """A computed preview plus the parsed snapshot it was built from."""

    preview: RolloutPreview
    status: ManagedRolloutStatus
    snapshot: WorkspaceRemoteSnapshot
    registry: list[PackageRegistryRequirement]
    fingerprint: str
    commit_sha: str


def target_role(workspace: Workspace) -> Role:
    """Return the role a rollout writes into a target workspace as.

    It is a service role bound to that workspace and its org, read from the
    database, with workspace admin scopes. It is never a platform superuser.
    """
    return Role(
        type="service",
        service_id="tracecat-service",
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        scopes=ADMIN_SCOPES,
    )


class ManagedRolloutEngine:
    """Preview and apply rollouts. Runs on an RLS-bypass session."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        transport_factory: VcsTransportFactory | None = None,
    ) -> None:
        self.session = session
        self._transport_factory = transport_factory

    # -- entry points ---------------------------------------------------
    async def preview(self, rollout_id: uuid.UUID) -> ManagedRollout:
        """Compute and store the preview of one rollout record."""
        rollout = await self._rollout(rollout_id)
        if rollout.status != ManagedRolloutStatus.PREVIEWING:
            return rollout
        try:
            if rollout.kind == ManagedRolloutKind.UNASSIGN:
                plan = await self._plan_unassign(rollout)
            else:
                plan = await self._plan_apply(rollout)
        except (TracecatValidationError, TracecatNotFoundError) as e:
            if rollout := await self._still_previewing(rollout_id):
                await self._finish(rollout, ManagedRolloutStatus.FAILED, error=str(e))
            return await self._rollout(rollout_id)
        if (rollout := await self._still_previewing(rollout_id)) is None:
            return await self._rollout(rollout_id)
        rollout.preview = plan.preview.model_dump(mode="json")
        rollout.state_fingerprint = plan.fingerprint
        rollout.commit_sha = plan.commit_sha or None
        await self._finish(rollout, plan.status)
        return rollout

    async def _still_previewing(self, rollout_id: uuid.UUID) -> ManagedRollout | None:
        """Lock and re-read the rollout; ``None`` if it left PREVIEWING.

        A newer preview of the same target marks this one STALE while it is
        still being computed. Writing the result back would revive it.
        """
        rollout = await self.session.scalar(
            select(ManagedRollout)
            .where(ManagedRollout.id == rollout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if rollout is None or rollout.status != ManagedRolloutStatus.PREVIEWING:
            await self.session.commit()
            return None
        return rollout

    async def apply(self, rollout_id: uuid.UUID) -> ManagedRollout:
        """Apply one previewed rollout record.

        Raises:
            StaleRolloutError: The target no longer matches the preview.
        """
        rollout = await self._rollout(rollout_id)
        if rollout.status != ManagedRolloutStatus.APPLYING:
            return rollout
        try:
            if rollout.kind == ManagedRolloutKind.UNASSIGN:
                plan = await self._plan_unassign(rollout)
                self._require_unchanged(rollout, plan)
                await self._apply_unassign(rollout)
                return rollout
            plan = await self._plan_apply(rollout)
            self._require_unchanged(rollout, plan)
            if plan.status not in (
                ManagedRolloutStatus.READY,
                ManagedRolloutStatus.NO_CHANGES,
            ):
                raise StaleRolloutError("The target is no longer ready; preview again")
            await self._apply_plan(rollout, plan)
        except StaleRolloutError as e:
            await self.session.rollback()
            rollout = await self._rollout(rollout_id)
            await self._finish(rollout, ManagedRolloutStatus.STALE, error=str(e))
        except Exception as e:
            await self.session.rollback()
            rollout = await self._rollout(rollout_id)
            logger.opt(exception=e).error(
                "Managed rollout failed", rollout_id=str(rollout_id)
            )
            await self._finish(rollout, ManagedRolloutStatus.FAILED, error=str(e))
        return rollout

    async def mark_failed(self, rollout_id: uuid.UUID, error: str) -> None:
        """Record a rollout that could not finish, unless it already did."""
        rollout = await self._rollout(rollout_id)
        if rollout.status in (
            ManagedRolloutStatus.PREVIEWING,
            ManagedRolloutStatus.APPLYING,
        ):
            await self._finish(rollout, ManagedRolloutStatus.FAILED, error=error)

    # -- planning -------------------------------------------------------
    async def _plan_apply(self, rollout: ManagedRollout) -> _Plan:
        source = await require_managed_source(self.session)
        workspace = await self._workspace(rollout.workspace_id)
        if workspace.id == source.workspace_id:
            raise TracecatValidationError("The source workspace cannot be a target")
        if rollout.tag is None:
            raise TracecatValidationError("The rollout has no tag")
        tag = await resolve_source_tag(
            self.session, source, rollout.tag, transport_factory=self._transport_factory
        )
        if tag is None:
            raise TracecatNotFoundError(f"Tag {rollout.tag!r} not found")
        tree = await read_source_commit(
            self.session,
            source,
            tag.commit_sha,
            transport_factory=self._transport_factory,
        )
        sync = self._sync(workspace, source.git_provider)
        await self._sync_managed_mappings(workspace.id)
        snapshot, diagnostics = await sync.parse_files(
            tree.files, commit_sha=tree.commit_sha, tree_sha=tree.tree_sha
        )
        preview = RolloutPreview(diagnostics=[d.message for d in diagnostics])
        if diagnostics:
            return _Plan(
                preview, ManagedRolloutStatus.FAILED, snapshot, [], "", tree.commit_sha
            )
        snapshot = await self._with_customer_trigger_status(workspace.id, snapshot)
        snapshot = await self._with_customer_secret_references(workspace.id, snapshot)
        managed = await self._managed_rows(workspace.id)
        current = await sync.project_workspace(
            create_missing_mappings=False, skip_unpublished=True
        )
        identities = await workspace_identities(self.session, workspace.id)

        workspace_id = workspace.id
        preview.conflicts = _conflicts(snapshot, identities, managed)
        registry = _package_registry(tree.files)
        preview.registry = await self._registry_plan(workspace, source, registry)
        matches = await self._requested_matches(workspace_id)
        # Workflow validation resolves actions against the target org's
        # registry. Install the package's registry versions in a savepoint for
        # the preview and roll it back; apply installs them for real.
        trial_install = any(
            p.action == "install" for p in preview.registry
        ) and not any(p.action == "blocked" for p in preview.registry)
        savepoint = await self.session.begin_nested() if trial_install else None
        try:
            if trial_install:
                with managed_write_bypass():
                    await self._install_registry(workspace, source, registry, None)
            prepared = await sync.prepare_pull_preview(
                snapshot,
                sync_schedules=False,
                requested_catalog_mappings=matches.catalog,
                requested_mcp_integration_mappings=matches.mcp,
                requested_secret_store_mappings=matches.stores,
            )
        finally:
            if savepoint is not None:
                await savepoint.rollback()
        result = prepared.preview
        preview.references = _references(result)
        preview.changes = [
            *_incoming_changes(result),
            *_deleted_changes(snapshot, managed, current),
        ]
        preview.delete_warnings = await self._delete_warnings(
            workspace_id, current, managed, preview.changes
        )
        preview.dropped_customer_skills = await self._dropped_customer_skills(
            workspace_id, snapshot, managed
        )
        if not result.success and not preview.references and not preview.conflicts:
            preview.diagnostics = [d.message for d in result.diagnostics]

        if preview.diagnostics:
            status = ManagedRolloutStatus.FAILED
        elif preview.conflicts or any(p.action == "blocked" for p in preview.registry):
            status = ManagedRolloutStatus.BLOCKED
        elif preview.references:
            status = ManagedRolloutStatus.NEEDS_REFERENCES
        elif not preview.changes and all(
            p.action == "present" for p in preview.registry
        ):
            status = ManagedRolloutStatus.NO_CHANGES
        else:
            status = ManagedRolloutStatus.READY
        return _Plan(
            preview=preview,
            status=status,
            snapshot=prepared.snapshot,
            registry=registry,
            fingerprint=_fingerprint(tree.commit_sha, managed, identities, matches),
            commit_sha=tree.commit_sha,
        )

    async def _plan_unassign(self, rollout: ManagedRollout) -> _Plan:
        workspace = await self._workspace(rollout.workspace_id)
        # Unassign reads nothing from the source repository, so it works even
        # after the source is unset. The provider only names a transport this
        # sync service never opens.
        sync = self._sync(workspace, VcsProvider.GITHUB)
        await self._sync_managed_mappings(workspace.id)
        managed = await self._managed_rows(workspace.id)
        current = await sync.project_workspace(
            create_missing_mappings=False, skip_unpublished=True
        )
        identities = await workspace_identities(self.session, workspace.id)
        empty = WorkspaceRemoteSnapshot(commit_sha="", files={}, spec=_empty_spec())
        changes = _deleted_changes(empty, managed, current)
        preview = RolloutPreview(
            changes=changes,
            delete_warnings=await self._delete_warnings(
                workspace.id, current, managed, changes
            ),
        )
        return _Plan(
            preview=preview,
            status=ManagedRolloutStatus.READY,
            snapshot=empty,
            registry=[],
            fingerprint=_fingerprint("", managed, identities, None),
            commit_sha="",
        )

    def _require_unchanged(self, rollout: ManagedRollout, plan: _Plan) -> None:
        if rollout.state_fingerprint != plan.fingerprint:
            raise StaleRolloutError("The target changed after the preview")

    # -- apply ----------------------------------------------------------
    async def _apply_plan(self, rollout: ManagedRollout, plan: _Plan) -> None:
        workspace = await self._workspace(rollout.workspace_id)
        source = await require_managed_source(self.session)
        sync = self._sync(workspace, source.git_provider)
        managed = await self._managed_rows(workspace.id)
        incoming_keys = _snapshot_keys(plan.snapshot)
        with managed_write_bypass():
            # Customer writes check ownership under this lock, shared. Holding
            # it exclusively means no customer write lands between the import
            # and the managed rows that lock what it imported.
            await hold_ownership_lock(self.session, workspace.id, shared=False)
            await self._install_registry(workspace, source, plan.registry, rollout)
            await self._drop_unneeded_registry(workspace, managed, plan.registry)
            customer_bindings = await self._customer_skill_bindings(
                workspace.id, managed
            )
            with preserve_customer_skill_bindings(customer_bindings):
                result = await sync.import_prepared_snapshot(
                    plan.snapshot, sync_schedules=False
                )
            if not result.success:
                raise TracecatValidationError(
                    "; ".join(d.message for d in result.diagnostics) or result.message
                )
            await self._record_managed(workspace.id, rollout, plan, incoming_keys)
            await self._record_package_skills(workspace.id, plan.snapshot)
            target = await self._target(rollout)
            target.last_applied_tag = rollout.tag
            target.last_rollout_at = datetime.now(UTC)
            await self.session.commit()
            # Re-read: recording can move a source id to a new local id, and
            # the old row for it is gone rather than removed from the package.
            removed = [
                row
                for row in await self._managed_rows(workspace.id)
                if row.resource_type != REGISTRY_RESOURCE_TYPE
                and (row.resource_type, row.source_id) not in incoming_keys
            ]
            rollout_id = rollout.id
            failures = await self._delete_managed(workspace, removed)
        # A failed delete rolls back, which expires every loaded object.
        rollout = await self._rollout(rollout_id)
        rollout.result = {
            "imported": {
                resource_type: count.imported
                for resource_type, count in (result.resource_counts or {}).items()
            },
            "deleted": len(removed) - len(failures),
        }
        if failures:
            await self._finish(
                rollout,
                ManagedRolloutStatus.FAILED,
                error="Could not delete: " + ", ".join(failures),
            )
            return
        rollout.applied_at = datetime.now(UTC)
        await self._finish(rollout, ManagedRolloutStatus.APPLIED)

    async def _apply_unassign(self, rollout: ManagedRollout) -> None:
        rollout_id, workspace_id = rollout.id, rollout.workspace_id
        workspace = await self._workspace(workspace_id)
        managed = await self._managed_rows(workspace_id)
        with managed_write_bypass():
            failures = await self._delete_managed(
                workspace,
                [row for row in managed if row.resource_type != REGISTRY_RESOURCE_TYPE],
            )
        # A failed delete rolls back, which expires every loaded object.
        rollout = await self._rollout(rollout_id)
        workspace = await self._workspace(workspace_id)
        managed = await self._managed_rows(workspace_id)
        if failures:
            await self._finish(
                rollout,
                ManagedRolloutStatus.FAILED,
                error="Could not delete: " + ", ".join(failures),
            )
            return
        # Detached copies and customer resources are untouched. A registry
        # repository the package installed is removed once no other workspace
        # in the org is assigned it.
        await self._release_registry(workspace, managed)
        for model in (
            ManagedResource,
            ManagedReferenceMatch,
            ManagedPresetSkillBinding,
        ):
            await self.session.execute(
                delete(model).where(model.workspace_id == workspace.id)
            )
        await self.session.execute(
            delete(WorkspaceSyncResourceMapping).where(
                WorkspaceSyncResourceMapping.workspace_id == workspace.id,
                WorkspaceSyncResourceMapping.provider == MANAGED_MAPPING_NAMESPACE,
            )
        )
        if rollout.target_id is not None:
            await self.session.execute(
                delete(ManagedTarget).where(ManagedTarget.id == rollout.target_id)
            )
        rollout.target_id = None
        rollout.applied_at = datetime.now(UTC)
        await self._finish(rollout, ManagedRolloutStatus.APPLIED)

    async def _record_managed(
        self,
        workspace_id: uuid.UUID,
        rollout: ManagedRollout,
        plan: _Plan,
        incoming_keys: set[tuple[str, str]],
    ) -> None:
        """Write a managed row for every resource the tag contains."""
        mappings = (
            await self.session.scalars(
                select(WorkspaceSyncResourceMapping).where(
                    WorkspaceSyncResourceMapping.workspace_id == workspace_id,
                    WorkspaceSyncResourceMapping.provider == MANAGED_MAPPING_NAMESPACE,
                )
            )
        ).all()
        rows = [
            {
                "workspace_id": workspace_id,
                "resource_type": mapping.resource_type,
                "local_id": mapping.local_id,
                "source_id": mapping.source_id,
                "source_path": mapping.source_path or mapping.source_id,
                "deployed_tag": rollout.tag,
                "deployed_commit_sha": plan.commit_sha,
                "deployed_at": func.now(),
                "rollout_id": rollout.id,
            }
            for mapping in mappings
            if (mapping.resource_type, mapping.source_id) in incoming_keys
        ]
        # A source id can move to a new local id, for example a renamed case
        # field. Drop its old row first so the upsert does not hit the
        # (type, source id) unique constraint.
        for row in rows:
            await self.session.execute(
                delete(ManagedResource).where(
                    ManagedResource.workspace_id == workspace_id,
                    ManagedResource.resource_type == row["resource_type"],
                    ManagedResource.source_id == row["source_id"],
                    ManagedResource.local_id != row["local_id"],
                )
            )
        for offset in range(0, len(rows), 500):
            stmt = insert(ManagedResource).values(rows[offset : offset + 500])
            await self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_managed_resource_local",
                    set_={
                        "source_id": stmt.excluded.source_id,
                        "source_path": stmt.excluded.source_path,
                        "deployed_tag": stmt.excluded.deployed_tag,
                        "deployed_commit_sha": stmt.excluded.deployed_commit_sha,
                        "deployed_at": stmt.excluded.deployed_at,
                        "rollout_id": stmt.excluded.rollout_id,
                    },
                )
            )

    async def _record_package_skills(
        self, workspace_id: uuid.UUID, snapshot: WorkspaceRemoteSnapshot
    ) -> None:
        """Record which skill bindings on each managed preset are the package's."""
        slugs = {
            binding.slug
            for preset in snapshot.spec.agent_presets.values()
            for binding in preset.skills
        }
        skill_ids = dict(
            (
                await self.session.execute(
                    select(Skill.slug, Skill.id).where(
                        Skill.workspace_id == workspace_id, Skill.slug.in_(slugs)
                    )
                )
            )
            .tuples()
            .all()
        )
        preset_ids = dict(
            (
                await self.session.execute(
                    select(AgentPreset.slug, AgentPreset.id).where(
                        AgentPreset.workspace_id == workspace_id,
                        AgentPreset.slug.in_(
                            [p.slug for p in snapshot.spec.agent_presets.values()]
                        ),
                    )
                )
            )
            .tuples()
            .all()
        )
        # Rewrite only the tag's presets. A preset leaving the package keeps
        # its rows until its delete succeeds.
        await self.session.execute(
            delete(ManagedPresetSkillBinding).where(
                ManagedPresetSkillBinding.workspace_id == workspace_id,
                ManagedPresetSkillBinding.preset_id.in_(list(preset_ids.values())),
            )
        )
        for preset in snapshot.spec.agent_presets.values():
            if (preset_id := preset_ids.get(preset.slug)) is None:
                continue
            for binding in preset.skills:
                if (skill_id := skill_ids.get(binding.slug)) is not None:
                    self.session.add(
                        ManagedPresetSkillBinding(
                            workspace_id=workspace_id,
                            preset_id=preset_id,
                            skill_id=skill_id,
                        )
                    )
        await self.session.flush()

    async def _delete_managed(
        self, workspace: Workspace, rows: Sequence[ManagedResource]
    ) -> list[str]:
        """Delete managed resources one by one; return the ones that failed."""
        role = target_role(workspace)
        workspace_id = workspace.id
        # Plain copies: a failed delete rolls back, which expires the rows.
        refs = [_RowRef.of(row) for row in rows]
        failures: list[str] = []
        for row in refs:
            try:
                await _delete_resource(self.session, role, row)
                await self.session.execute(
                    delete(ManagedResource).where(ManagedResource.id == row.id)
                )
                await self.session.execute(
                    delete(WorkspaceSyncResourceMapping).where(
                        WorkspaceSyncResourceMapping.workspace_id == workspace_id,
                        WorkspaceSyncResourceMapping.provider
                        == MANAGED_MAPPING_NAMESPACE,
                        WorkspaceSyncResourceMapping.resource_type == row.resource_type,
                        WorkspaceSyncResourceMapping.local_id == row.local_id,
                    )
                )
                await self.session.commit()
            except Exception as e:
                await self.session.rollback()
                logger.opt(exception=e).warning(
                    "Could not delete managed resource",
                    resource_type=row.resource_type,
                    local_id=str(row.local_id),
                )
                failures.append(f"{row.resource_type} {row.source_id}")
        return failures

    # -- registry -------------------------------------------------------
    async def _registry_plan(
        self,
        workspace: Workspace,
        source: ManagedSource,
        requirements: Sequence[PackageRegistryRequirement],
    ) -> list[RolloutRegistryPlan]:
        plans: list[RolloutRegistryPlan] = []
        for requirement in requirements:
            plans.append(await self._registry_step(workspace, source, requirement))
        return plans

    async def _registry_step(
        self,
        workspace: Workspace,
        source: ManagedSource,
        requirement: PackageRegistryRequirement,
    ) -> RolloutRegistryPlan:
        def plan(action: str, reason: str | None = None) -> RolloutRegistryPlan:
            return RolloutRegistryPlan.model_validate(
                {
                    "origin": requirement.origin,
                    "version": requirement.version,
                    "action": action,
                    "reason": reason,
                }
            )

        if await self._source_version(source, requirement) is None:
            return plan("blocked", "The source registry no longer has this version")
        repo = await self.session.scalar(
            select(RegistryRepository).where(
                RegistryRepository.organization_id == workspace.organization_id,
                RegistryRepository.origin == requirement.origin,
            )
        )
        installed_by = (
            (
                await self.session.execute(
                    select(
                        ManagedResource.workspace_id, ManagedResource.source_path
                    ).where(
                        ManagedResource.resource_type == REGISTRY_RESOURCE_TYPE,
                        ManagedResource.local_id == repo.id,
                    )
                )
            )
            .tuples()
            .all()
            if repo is not None
            else []
        )
        if repo is not None and not installed_by:
            return plan(
                "blocked",
                "The customer organization has its own registry repository "
                "with this origin",
            )
        if other := next(
            (
                version
                for workspace_id, version in installed_by
                if workspace_id != workspace.id and version != requirement.version
            ),
            None,
        ):
            return plan(
                "blocked",
                f"Another workspace in this organization needs version {other}",
            )
        if repo is not None and repo.current_version_id is not None:
            current = await self.session.scalar(
                select(RegistryVersion).where(
                    RegistryVersion.id == repo.current_version_id
                )
            )
            if current is not None and current.version == requirement.version:
                return plan("present")
        return plan("install")

    async def _source_version(
        self, source: ManagedSource, requirement: PackageRegistryRequirement
    ) -> RegistryVersion | None:
        return await self.session.scalar(
            select(RegistryVersion)
            .join(
                RegistryRepository,
                RegistryRepository.id == RegistryVersion.repository_id,
            )
            .where(
                RegistryRepository.organization_id == source.organization_id,
                RegistryRepository.origin == requirement.origin,
                RegistryVersion.version == requirement.version,
            )
        )

    async def _install_registry(
        self,
        workspace: Workspace,
        source: ManagedSource,
        requirements: Sequence[PackageRegistryRequirement],
        rollout: ManagedRollout | None,
    ) -> None:
        """Make each required registry version current in the target org.

        Copies the source org's immutable version (tarball URI, manifest and
        action index) instead of syncing from Git, so the target org needs no
        credentials for the custom registry repository.
        """
        role = Role(
            type="service",
            service_id="tracecat-service",
            organization_id=workspace.organization_id,
            scopes=ADMIN_SCOPES,
        )
        for requirement in requirements:
            step = await self._registry_step(workspace, source, requirement)
            if step.action == "blocked":
                raise TracecatValidationError(step.reason or "Registry install blocked")
            source_version = await self._source_version(source, requirement)
            if source_version is None:
                raise TracecatValidationError("Source registry version not found")
            repo = await self.session.scalar(
                select(RegistryRepository).where(
                    RegistryRepository.organization_id == workspace.organization_id,
                    RegistryRepository.origin == requirement.origin,
                )
            )
            if repo is None:
                # Not RegistryReposService.create_repository, which commits:
                # the install belongs to the rollout's transaction.
                repo = RegistryRepository(
                    organization_id=workspace.organization_id,
                    origin=requirement.origin,
                )
                self.session.add(repo)
                await self.session.flush()
            versions = RegistryVersionsService(self.session, role)
            version = await versions.get_version_by_repo_and_version(
                repo.id, requirement.version
            )
            if version is None:
                version = await versions.create_version(
                    RegistryVersionCreate(
                        repository_id=repo.id,
                        version=source_version.version,
                        commit_sha=source_version.commit_sha,
                        manifest=RegistryVersionManifest.model_validate(
                            source_version.manifest
                        ),
                        tarball_uri=source_version.tarball_uri,
                    ),
                    commit=False,
                )
                await versions.populate_index_from_manifest(version, commit=False)
            repo.current_version_id = version.id
            self.session.add(repo)
            stmt = insert(ManagedResource).values(
                workspace_id=workspace.id,
                resource_type=REGISTRY_RESOURCE_TYPE,
                local_id=repo.id,
                source_id=requirement.origin,
                source_path=requirement.version,
                deployed_tag=rollout.tag if rollout and rollout.tag else "",
                deployed_commit_sha=requirement.commit_sha or "",
                rollout_id=rollout.id if rollout else None,
            )
            await self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_managed_resource_local",
                    set_={
                        "source_id": stmt.excluded.source_id,
                        "source_path": stmt.excluded.source_path,
                        "deployed_tag": stmt.excluded.deployed_tag,
                        "deployed_commit_sha": stmt.excluded.deployed_commit_sha,
                        "deployed_at": func.now(),
                        "rollout_id": stmt.excluded.rollout_id,
                    },
                )
            )

    async def _drop_unneeded_registry(
        self,
        workspace: Workspace,
        managed: Sequence[ManagedResource],
        requirements: Sequence[PackageRegistryRequirement],
    ) -> None:
        """Stop owning registries the tag no longer needs.

        Each one is uninstalled once no other workspace needs it either.
        """
        needed = {requirement.origin for requirement in requirements}
        obsolete = [
            row
            for row in managed
            if row.resource_type == REGISTRY_RESOURCE_TYPE
            and row.source_id not in needed
        ]
        if not obsolete:
            return
        await self.session.execute(
            delete(ManagedResource).where(
                ManagedResource.id.in_([row.id for row in obsolete])
            )
        )
        await self._release_registry(workspace, obsolete)

    async def _release_registry(
        self, workspace: Workspace, managed: Sequence[ManagedResource]
    ) -> None:
        """Uninstall registry repositories only this workspace was assigned."""
        repo_ids = [
            row.local_id
            for row in managed
            if row.resource_type == REGISTRY_RESOURCE_TYPE
        ]
        if not repo_ids:
            return
        still_used = set(
            await self.session.scalars(
                select(ManagedResource.local_id).where(
                    ManagedResource.resource_type == REGISTRY_RESOURCE_TYPE,
                    ManagedResource.local_id.in_(repo_ids),
                    ManagedResource.workspace_id != workspace.id,
                )
            )
        )
        for repo_id in repo_ids:
            if repo_id in still_used:
                continue
            repo = await self.session.scalar(
                select(RegistryRepository).where(
                    RegistryRepository.id == repo_id,
                    RegistryRepository.organization_id == workspace.organization_id,
                )
            )
            if repo is None:
                continue
            # The repository and its current version point at each other.
            # Clear the pointer first, as RegistryReposService.delete_repository
            # does.
            repo.current_version_id = None
            await self.session.flush()
            await self.session.delete(repo)

    # -- customer state ---------------------------------------------------
    async def _customer_skill_bindings(
        self, workspace_id: uuid.UUID, managed: Sequence[ManagedResource]
    ) -> dict[uuid.UUID, list[SkillBindingSpec]]:
        """Return, per managed preset, the skill bindings the customer added."""
        preset_ids = [
            row.local_id
            for row in managed
            if row.resource_type == SyncResourceType.AGENT_PRESET.value
        ]
        if not preset_ids:
            return {}
        package = set(
            (
                await self.session.execute(
                    select(
                        ManagedPresetSkillBinding.preset_id,
                        ManagedPresetSkillBinding.skill_id,
                    ).where(ManagedPresetSkillBinding.preset_id.in_(preset_ids))
                )
            )
            .tuples()
            .all()
        )
        bindings: dict[uuid.UUID, list[SkillBindingSpec]] = {}
        rows = (
            (
                await self.session.execute(
                    select(
                        AgentPresetSkill.preset_id,
                        AgentPresetSkill.skill_id,
                        AgentPresetSkill.skill_version_id,
                    ).where(
                        AgentPresetSkill.workspace_id == workspace_id,
                        AgentPresetSkill.preset_id.in_(preset_ids),
                    )
                )
            )
            .tuples()
            .all()
        )
        for preset_id, skill_id, version_id in rows:
            if (preset_id, skill_id) in package:
                continue
            bindings.setdefault(preset_id, []).append(
                SkillBindingSpec(skill_id=skill_id, skill_version_id=version_id)
            )
        return bindings

    async def _dropped_customer_skills(
        self,
        workspace_id: uuid.UUID,
        snapshot: WorkspaceRemoteSnapshot,
        managed: Sequence[ManagedResource],
    ) -> list[RolloutDroppedSkill]:
        """Customer bindings the package now binds itself, so the package's wins."""
        customer = await self._customer_skill_bindings(workspace_id, managed)
        if not customer:
            return []
        skill_slugs = dict(
            (
                await self.session.execute(
                    select(Skill.id, Skill.slug).where(
                        Skill.id.in_(
                            [b.skill_id for specs in customer.values() for b in specs]
                        )
                    )
                )
            )
            .tuples()
            .all()
        )
        preset_slugs = dict(
            (
                await self.session.execute(
                    select(AgentPreset.id, AgentPreset.slug).where(
                        AgentPreset.id.in_(list(customer))
                    )
                )
            )
            .tuples()
            .all()
        )
        dropped: list[RolloutDroppedSkill] = []
        incoming = {
            preset.slug: {binding.slug for binding in preset.skills}
            for preset in snapshot.spec.agent_presets.values()
        }
        for preset_id, specs in customer.items():
            preset_slug = preset_slugs.get(preset_id)
            for binding in specs:
                slug = skill_slugs.get(binding.skill_id)
                if preset_slug and slug and slug in incoming.get(preset_slug, set()):
                    dropped.append(RolloutDroppedSkill(preset=preset_slug, skill=slug))
        return dropped

    async def _with_customer_trigger_status(
        self, workspace_id: uuid.UUID, snapshot: WorkspaceRemoteSnapshot
    ) -> WorkspaceRemoteSnapshot:
        """Keep the target's webhook and form on/off status for managed workflows.

        Customers own those toggles. A workflow the rollout creates starts in
        the package's state.
        """
        mappings = dict(
            (
                await self.session.execute(
                    select(
                        WorkspaceSyncResourceMapping.source_id,
                        WorkspaceSyncResourceMapping.local_id,
                    ).where(
                        WorkspaceSyncResourceMapping.workspace_id == workspace_id,
                        WorkspaceSyncResourceMapping.provider
                        == MANAGED_MAPPING_NAMESPACE,
                        WorkspaceSyncResourceMapping.resource_type
                        == SyncResourceType.WORKFLOW.value,
                    )
                )
            )
            .tuples()
            .all()
        )
        if not mappings:
            return snapshot
        webhook_status = dict(
            (
                await self.session.execute(
                    select(Webhook.workflow_id, Webhook.status).where(
                        Webhook.workflow_id.in_(list(mappings.values()))
                    )
                )
            )
            .tuples()
            .all()
        )
        form_status = dict(
            (
                await self.session.execute(
                    select(Form.workflow_id, Form.status).where(
                        Form.workflow_id.in_(list(mappings.values()))
                    )
                )
            )
            .tuples()
            .all()
        )
        workflows: dict[str, WorkflowResourceSpec] = {}
        for source_id, spec in snapshot.spec.workflows.items():
            update: dict[str, object] = {"schedules": None}
            if (local_id := mappings.get(source_id)) is None:
                workflows[source_id] = spec.model_copy(update=update)
                continue
            if spec.webhook and (status := webhook_status.get(local_id)):
                update["webhook"] = spec.webhook.model_copy(update={"status": status})
            if spec.form and (status := form_status.get(local_id)):
                update["form"] = spec.form.model_copy(update={"status": status})
            workflows[source_id] = spec.model_copy(update=update)
        return snapshot.model_copy(
            update={"spec": snapshot.spec.model_copy(update={"workflows": workflows})}
        )

    async def _with_customer_secret_references(
        self, workspace_id: uuid.UUID, snapshot: WorkspaceRemoteSnapshot
    ) -> WorkspaceRemoteSnapshot:
        """Keep the target's own AWS secret name or ARN on managed secrets.

        The package matches the store; the customer points the secret at
        their own secret, so a rollout must not overwrite that reference. A
        secret the rollout creates starts with the package's reference.
        """
        mappings = dict(
            (
                await self.session.execute(
                    select(
                        WorkspaceSyncResourceMapping.source_id,
                        WorkspaceSyncResourceMapping.local_id,
                    ).where(
                        WorkspaceSyncResourceMapping.workspace_id == workspace_id,
                        WorkspaceSyncResourceMapping.provider
                        == MANAGED_MAPPING_NAMESPACE,
                        WorkspaceSyncResourceMapping.resource_type
                        == SyncResourceType.SECRET_METADATA.value,
                    )
                )
            )
            .tuples()
            .all()
        )
        if not mappings:
            return snapshot
        references = dict(
            (
                await self.session.execute(
                    select(Secret.id, Secret.remote_reference).where(
                        Secret.id.in_(list(mappings.values())),
                        Secret.remote_reference.is_not(None),
                    )
                )
            )
            .tuples()
            .all()
        )
        secrets = {
            source_id: spec.model_copy(update={"remote_reference": reference})
            if spec.remote_reference is not None
            and (local_id := mappings.get(source_id)) is not None
            and (reference := references.get(local_id)) is not None
            else spec
            for source_id, spec in snapshot.spec.secret_metadata.items()
        }
        return snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"secret_metadata": secrets})
            }
        )

    async def _delete_warnings(
        self,
        workspace_id: uuid.UUID,
        current: WorkspaceProjection,
        managed: Sequence[ManagedResource],
        changes: Sequence[RolloutChange],
    ) -> list[RolloutDeleteWarning]:
        """Describe the customer data each delete destroys."""
        by_key = {(row.resource_type, row.source_id): row for row in managed}
        managed_local = {(row.resource_type, row.local_id) for row in managed}
        customer_text = [
            (name, text)
            for name, text in _resource_texts(current)
            if name[0] not in managed_local
        ]
        warnings: list[RolloutDeleteWarning] = []
        for change in changes:
            if change.change != "deleted":
                continue
            row = by_key.get((change.resource_type.value, change.source_id))
            if row is None:
                continue
            warning = RolloutDeleteWarning(
                resource_type=change.resource_type,
                source_id=change.source_id,
                name=change.name,
                referenced_by=sorted(
                    label for (_, label), text in customer_text if change.name in text
                ),
            )
            match change.resource_type:
                case SyncResourceType.TABLE:
                    warning.table_rows = await self._table_rows(row.local_id)
                case SyncResourceType.CASE_TAG:
                    warning.cases_using = await self.session.scalar(
                        select(func.count()).where(CaseTagLink.tag_id == row.local_id)
                    )
                case SyncResourceType.CASE_DROPDOWN:
                    warning.cases_using = await self.session.scalar(
                        select(func.count()).where(
                            CaseDropdownValue.definition_id == row.local_id
                        )
                    )
                case SyncResourceType.CASE_FIELD:
                    warning.cases_using = await self.session.scalar(
                        select(func.count()).where(Case.workspace_id == workspace_id)
                    )
                case SyncResourceType.SECRET_METADATA | SyncResourceType.VARIABLE:
                    warning.has_customer_values = True
                case _:
                    pass
            warnings.append(warning)
        return warnings

    async def _table_rows(self, table_id: uuid.UUID) -> int | None:
        table = await self.session.scalar(select(Table).where(Table.id == table_id))
        if table is None:
            return None
        workspace = await self._workspace(table.workspace_id)
        return await TablesService(
            self.session, role=target_role(workspace)
        ).count_rows(table)

    # -- helpers ----------------------------------------------------------
    def _sync(
        self, workspace: Workspace, provider: VcsProvider
    ) -> WorkspaceSyncService:
        return WorkspaceSyncService(
            self.session,
            target_role(workspace),
            provider=provider,
            mapping_namespace=MANAGED_MAPPING_NAMESPACE,
        )

    async def _sync_managed_mappings(self, workspace_id: uuid.UUID) -> None:
        """Make the ``managed`` mapping namespace mirror the managed table.

        The importer pairs repository resources with local rows through sync
        mappings. Rebuilding them from ``managed_resource`` means a rollout
        matches only resources it owns.
        """
        await self.session.execute(
            delete(WorkspaceSyncResourceMapping).where(
                WorkspaceSyncResourceMapping.workspace_id == workspace_id,
                WorkspaceSyncResourceMapping.provider == MANAGED_MAPPING_NAMESPACE,
            )
        )
        rows = [
            {
                "workspace_id": workspace_id,
                "provider": MANAGED_MAPPING_NAMESPACE,
                "resource_type": row.resource_type,
                "source_id": row.source_id,
                "source_path": row.source_path,
                "local_id": row.local_id,
            }
            for row in await self._managed_rows(workspace_id)
            if row.resource_type != REGISTRY_RESOURCE_TYPE
        ]
        if rows:
            await self.session.execute(
                insert(WorkspaceSyncResourceMapping).values(rows)
            )
        await self.session.flush()

    async def _requested_matches(self, workspace_id: uuid.UUID) -> _Matches:
        """Return the saved reference matches as pull mapping requests."""
        catalog: dict[uuid.UUID, uuid.UUID] = {}
        mcp: dict[uuid.UUID, uuid.UUID] = {}
        stores: dict[str, uuid.UUID | None] = {}
        for saved in await self.session.scalars(
            select(ManagedReferenceMatch).where(
                ManagedReferenceMatch.workspace_id == workspace_id
            )
        ):
            match saved.reference_kind:
                case ManagedReferenceKind.MODEL if saved.resolved_id:
                    if (key := _uuid_or_none(saved.source_key)) is not None:
                        catalog[key] = saved.resolved_id
                case ManagedReferenceKind.MCP_INTEGRATION if saved.resolved_id:
                    if (key := _uuid_or_none(saved.source_key)) is not None:
                        mcp[key] = saved.resolved_id
                case ManagedReferenceKind.SECRET_STORE:
                    stores[saved.source_key] = saved.resolved_id
                case _:
                    pass
        return _Matches(catalog=catalog, mcp=mcp, stores=stores)

    async def _managed_rows(self, workspace_id: uuid.UUID) -> list[ManagedResource]:
        return list(
            await self.session.scalars(
                select(ManagedResource).where(
                    ManagedResource.workspace_id == workspace_id
                )
            )
        )

    async def _rollout(self, rollout_id: uuid.UUID) -> ManagedRollout:
        rollout = await self.session.scalar(
            select(ManagedRollout)
            .where(ManagedRollout.id == rollout_id)
            .execution_options(populate_existing=True)
        )
        if rollout is None:
            raise TracecatNotFoundError("Rollout not found")
        return rollout

    async def _target(self, rollout: ManagedRollout) -> ManagedTarget:
        target = await self.session.scalar(
            select(ManagedTarget).where(ManagedTarget.id == rollout.target_id)
        )
        if target is None:
            raise TracecatNotFoundError("Target not found")
        return target

    async def _workspace(self, workspace_id: uuid.UUID) -> Workspace:
        workspace = await self.session.scalar(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        if workspace is None:
            raise TracecatNotFoundError("Target workspace not found")
        return workspace

    async def _finish(
        self,
        rollout: ManagedRollout,
        status: ManagedRolloutStatus,
        *,
        error: str | None = None,
    ) -> None:
        rollout.status = status
        rollout.error = error
        rollout.updated_at = datetime.now(UTC)
        if rollout.target_id is not None and (
            target_status := _TARGET_STATUS_BY_ROLLOUT.get(status)
        ):
            target = await self.session.scalar(
                select(ManagedTarget).where(ManagedTarget.id == rollout.target_id)
            )
            if target is not None and not (
                rollout.kind == ManagedRolloutKind.UNASSIGN
                and status != ManagedRolloutStatus.FAILED
            ):
                target.last_rollout_status = target_status
                if status in (
                    ManagedRolloutStatus.APPLIED,
                    ManagedRolloutStatus.FAILED,
                ):
                    target.last_rollout_at = datetime.now(UTC)
        await self.session.commit()
        if status in _AUDITED_OUTCOMES:
            await self._audit_outcome(rollout)

    async def _audit_outcome(self, rollout: ManagedRollout) -> None:
        """Record an applied, blocked or failed rollout in both audit logs."""
        workspace = await self.session.scalar(
            select(Workspace).where(Workspace.id == rollout.workspace_id)
        )
        preview = (
            RolloutPreview.model_validate(rollout.preview)
            if rollout.preview
            else RolloutPreview()
        )
        counts = {
            kind: sum(1 for change in preview.changes if change.change == kind)
            for kind in ("added", "modified", "deleted")
        }
        await audit_managed_event(
            self.session,
            actor=PlatformRole(
                type="user", user_id=rollout.created_by, service_id="tracecat-api"
            )
            if rollout.created_by
            else None,
            resource_type="managed_rollout",
            action="delete" if rollout.kind == ManagedRolloutKind.UNASSIGN else "apply",
            resource_id=rollout.id,
            status=AuditEventStatus.SUCCESS
            if rollout.status == ManagedRolloutStatus.APPLIED
            else AuditEventStatus.FAILURE,
            data={
                "operation": rollout.status.value,
                "release_tag": rollout.tag,
                "commit_id": rollout.commit_sha,
                "target_id": str(rollout.target_id) if rollout.target_id else None,
                "added_count": counts["added"],
                "modified_count": counts["modified"],
                "deleted_count": counts["deleted"],
            },
            target_organization_id=workspace.organization_id if workspace else None,
            target_workspace_id=rollout.workspace_id,
        )


# -- pure helpers -----------------------------------------------------------
def _empty_spec() -> WorkspaceSpec:
    return workspace_spec_from_maps(
        {adapter.spec_attr: {} for adapter in WORKSPACE_RESOURCE_ADAPTERS}
    )


def _snapshot_keys(snapshot: WorkspaceRemoteSnapshot) -> set[tuple[str, str]]:
    return {
        (adapter.resource_type.value, source_id)
        for adapter in WORKSPACE_RESOURCE_ADAPTERS
        for source_id in adapter.specs(snapshot.spec)
    }


def _conflicts(
    snapshot: WorkspaceRemoteSnapshot,
    identities: Mapping[IdentityKey, uuid.UUID],
    managed: Sequence[ManagedResource],
) -> list[RolloutConflict]:
    """Return incoming resources whose name a customer resource already uses."""
    managed_local = {(row.resource_type, row.local_id) for row in managed}
    managed_source = {(row.resource_type, row.source_id) for row in managed}
    conflicts: list[RolloutConflict] = []
    for adapter in WORKSPACE_RESOURCE_ADAPTERS:
        resource_type = adapter.resource_type.value
        for source_id, spec in adapter.specs(snapshot.spec).items():
            if (resource_type, source_id) in managed_source:
                continue
            identity = spec_identity(adapter.resource_type, spec)
            if identity is None:
                continue
            candidates = [identity]
            # The case field importer also adopts an existing field whose
            # name equals the source id, and renames it out of the way.
            if (
                adapter.resource_type == SyncResourceType.CASE_FIELD
                and (source_id,) != identity
            ):
                candidates.append((source_id,))
            for candidate in candidates:
                existing = identities.get((resource_type, candidate))
                if existing is None or (resource_type, existing) in managed_local:
                    continue
                conflicts.append(
                    RolloutConflict(
                        resource_type=adapter.resource_type,
                        source_id=source_id,
                        name="/".join(candidate),
                        existing_local_id=existing,
                    )
                )
                break
    return conflicts


def _references(result: PullResult) -> list[RolloutReference]:
    references = [
        RolloutReference(
            kind=ManagedReferenceKind.MODEL,
            source_key=str(r.source_catalog_id),
            label=f"{r.model_provider}/{r.model_name}",
        )
        for r in result.catalog_mapping_requirements or []
    ]
    references.extend(
        RolloutReference(
            kind=ManagedReferenceKind.MCP_INTEGRATION,
            source_key=str(r.source_mcp_integration_id),
            label=r.name or r.slug or str(r.source_mcp_integration_id),
        )
        for r in result.mcp_integration_mapping_requirements or []
    )
    references.extend(
        RolloutReference(
            kind=ManagedReferenceKind.SECRET_STORE,
            source_key=r.source_store,
            label=r.source_store,
        )
        for r in result.secret_store_mapping_requirements or []
    )
    return references


def _incoming_changes(result: PullResult) -> list[RolloutChange]:
    return [
        RolloutChange(
            change=diff.change_type,
            resource_type=SyncResourceType(diff.resource_type),
            source_id=diff.source_id,
            name=diff.title or diff.source_id,
        )
        for diff in result.resource_diffs or []
        if diff.change_type != "deleted"
    ]


def _deleted_changes(
    snapshot: WorkspaceRemoteSnapshot,
    managed: Sequence[ManagedResource],
    current: WorkspaceProjection,
) -> list[RolloutChange]:
    incoming = _snapshot_keys(snapshot)
    names = {key: label for (key, label), _ in _resource_texts(current)}
    changes: list[RolloutChange] = []
    for row in managed:
        if row.resource_type == REGISTRY_RESOURCE_TYPE:
            continue
        if (row.resource_type, row.source_id) in incoming:
            continue
        changes.append(
            RolloutChange(
                change="deleted",
                resource_type=SyncResourceType(row.resource_type),
                source_id=row.source_id,
                name=names.get((row.resource_type, row.local_id), row.source_id),
            )
        )
    return changes


def _resource_texts(
    projection: WorkspaceProjection,
) -> list[tuple[tuple[tuple[str, uuid.UUID], str], str]]:
    """Return ``((type, local_id), label)`` and the serialized text of each resource."""
    local_by_key = {
        (target.resource_type, target.source_id): target.local_id
        for target in projection.mapping_targets
    }
    out: list[tuple[tuple[tuple[str, uuid.UUID], str], str]] = []
    for adapter in WORKSPACE_RESOURCE_ADAPTERS:
        for source_id, spec in adapter.specs(projection.spec).items():
            local_id = local_by_key.get((adapter.resource_type.value, source_id))
            if local_id is None:
                continue
            label = adapter.display_name(spec) or source_id
            if isinstance(spec, WorkflowResourceSpec):
                label = spec.definition.title or spec.alias or source_id
            out.append(
                (
                    ((adapter.resource_type.value, local_id), label),
                    spec.model_dump_json(),
                )
            )
    return out


def _package_registry(files: Mapping[str, str]) -> list[PackageRegistryRequirement]:
    content = files.get(MANIFEST_FILENAME)
    if not content:
        return []
    package = workspace_manifest_from_json(content).package
    return list(package.registry) if package else []


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _fingerprint(
    commit_sha: str,
    managed: Sequence[ManagedResource],
    identities: Mapping[IdentityKey, uuid.UUID],
    matches: _Matches | None,
) -> str:
    """Hash what apply must find unchanged: the tag commit, the target state
    and the saved reference matches the preview resolved with."""
    return hashlib.sha256(
        orjson.dumps(
            {
                "commit": commit_sha,
                "matches": None
                if matches is None
                else {
                    "catalog": sorted(
                        (str(k), str(v)) for k, v in matches.catalog.items()
                    ),
                    "mcp": sorted((str(k), str(v)) for k, v in matches.mcp.items()),
                    "stores": sorted((k, str(v)) for k, v in matches.stores.items()),
                },
                "managed": sorted(
                    (row.resource_type, str(row.local_id), row.source_id)
                    for row in managed
                ),
                "resources": sorted(
                    (resource_type, list(identity), str(local_id))
                    for (resource_type, identity), local_id in identities.items()
                ),
            },
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _RowRef:
    """The fields of a managed row a delete needs, detached from the session."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    resource_type: str
    local_id: uuid.UUID
    source_id: str

    @classmethod
    def of(cls, row: ManagedResource) -> _RowRef:
        return cls(
            id=row.id,
            workspace_id=row.workspace_id,
            resource_type=row.resource_type,
            local_id=row.local_id,
            source_id=row.source_id,
        )


async def _delete_resource(session: AsyncSession, role: Role, row: _RowRef) -> None:
    """Delete one resource through its own service, so cleanup runs as usual."""
    local_id = row.local_id
    match SyncResourceType(row.resource_type):
        case SyncResourceType.WORKFLOW:
            await WorkflowsManagementService(session, role=role).delete_workflow(
                WorkflowUUID.new(local_id)
            )
        case SyncResourceType.AGENT_PRESET:
            if preset := await session.scalar(
                select(AgentPreset).where(AgentPreset.id == local_id)
            ):
                await AgentPresetService(session, role=role).delete_preset(preset)
        case SyncResourceType.SKILL:
            await SkillService(session, role=role).archive_skill(local_id)
        case SyncResourceType.TABLE:
            if table := await session.scalar(select(Table).where(Table.id == local_id)):
                await TablesService(session, role=role).delete_table(table)
        case SyncResourceType.CASE_TAG:
            if tag := await session.scalar(
                select(CaseTag).where(CaseTag.id == local_id)
            ):
                await CaseTagsService(session, role=role).delete_tag(tag)
        case SyncResourceType.CASE_DROPDOWN:
            if definition := await session.scalar(
                select(CaseDropdownDefinition).where(
                    CaseDropdownDefinition.id == local_id
                )
            ):
                await CaseDropdownDefinitionsService(
                    session, role=role
                ).delete_definition(definition)
        case SyncResourceType.CASE_DURATION:
            await CaseDurationDefinitionService(session, role=role).delete_definition(
                local_id
            )
        case SyncResourceType.CASE_FIELD:
            # A case field's local id derives from its schema key, which need
            # not equal the package source id. Delete the key that derives to
            # this row's local id.
            fields = await session.scalar(
                select(CaseFields).where(CaseFields.workspace_id == row.workspace_id)
            )
            if fields is not None and (
                key := next(
                    (
                        name
                        for name in (fields.schema or {})
                        if uuid.uuid5(fields.id, name) == local_id
                    ),
                    None,
                )
            ):
                await CaseFieldsService(session, role=role).delete_field(key)
        case SyncResourceType.VARIABLE:
            if variable := await session.scalar(
                select(WorkspaceVariable).where(WorkspaceVariable.id == local_id)
            ):
                await VariablesService(session, role=role).delete_variable(variable)
        case SyncResourceType.SECRET_METADATA:
            if secret := await session.scalar(
                select(Secret).where(Secret.id == local_id)
            ):
                await SecretsService(session, role=role).delete_secret(secret)
