"""Workspace Git sync projection, reconciliation, and ChangeSet service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert

from tracecat.db.models import (
    Workflow,
    Workspace,
    WorkspaceSyncChangeSet,
    WorkspaceSyncChangeSetItem,
    WorkspaceSyncMaterialization,
    WorkspaceSyncResourceMapping,
    WorkspaceSyncState,
)
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.git.utils import parse_git_url
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic, PullOptions, PullResult, PushOptions
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import WorkflowDslPublishResult
from tracecat.workspace_sync.enums import (
    ChangeSetStatus,
    MaterializationStatus,
    ResourceSyncStatus,
    SyncDirection,
    SyncOperation,
    SyncProvider,
    SyncResourceType,
    SyncStateStatus,
    ValidationStatus,
)
from tracecat.workspace_sync.git import WorkspaceGitHubSyncService
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    ChangeSetCreate,
    ChangeSetExport,
    ChangeSetRead,
    ResourceRef,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportResult,
    WorkspaceSyncPendingChange,
    WorkspaceSyncPendingChanges,
    WorkspaceSyncStatus,
)
from tracecat.workspace_sync.serialization import (
    canonical_json_text,
    stable_hash,
)
from tracecat.workspace_sync.workflow import (
    default_workflow_source_id,
    is_workflow_definition_path,
    parse_workflow_spec,
    serialize_workflow_spec,
    workflow_source_path,
    workflow_spec_from_orm,
    workflow_spec_to_remote,
)
from tracecat.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class PullReconciliationPlan:
    """Per-resource change sets driving pull reconciliation and status."""

    local_changed: set[str]
    local_deleted: set[str]
    remote_changed: set[str]
    remote_deleted: set[str]
    convergent: set[str]
    resurrect: set[str]
    conflicts: list[str]
    to_import: set[str]
    untrack: set[str]


class WorkspaceGitSyncService(BaseWorkspaceService):
    """Workspace-level Git sync service."""

    service_name = "workspace_git_sync"

    async def project_workspace(
        self,
        *,
        workflow_ids: Sequence[WorkflowUUID] | None = None,
        create_missing_mappings: bool = True,
    ) -> WorkspaceProjection:
        workflows = await self._list_projectable_workflows(workflow_ids=workflow_ids)
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        mgmt_service = WorkflowsManagementService(session=self.session, role=self.role)
        specs: dict[str, WorkflowResourceSpec] = {}

        for workflow in workflows:
            await self.session.refresh(
                workflow,
                ["tags", "folder", "schedules", "webhook", "case_trigger"],
            )
            dsl = await self._get_workflow_dsl(
                workflow,
                defn_service=defn_service,
                mgmt_service=mgmt_service,
            )
            preferred_source_id = default_workflow_source_id(
                alias=workflow.alias,
                title=dsl.title,
            )
            mapping = await self._ensure_resource_mapping(
                resource_type=SyncResourceType.WORKFLOW.value,
                local_id=WorkflowUUID.new(workflow.id),
                preferred_source_id=preferred_source_id,
                source_path=workflow_source_path(preferred_source_id),
                create=create_missing_mappings,
                reserved_source_ids=set(specs),
            )
            if mapping is not None:
                source_id = mapping.source_id
            else:
                # Read-only projection: apply the same dedup as mapping creation
                # so both paths assign identical source ids.
                source_id = await self._unique_source_id(
                    resource_type=SyncResourceType.WORKFLOW.value,
                    preferred_source_id=preferred_source_id,
                    reserved_source_ids=set(specs),
                )
            spec = workflow_spec_from_orm(workflow, dsl=dsl, source_id=source_id)
            specs[source_id] = spec

        manifest = WorkspaceManifest()
        spec = WorkspaceSpec(workflows=dict(sorted(specs.items())))
        files = self._files_from_spec(manifest=manifest, spec=spec)
        return WorkspaceProjection(
            manifest=manifest,
            spec=spec,
            files=files,
            spec_hash=stable_hash(spec),
        )

    async def parse_files(
        self,
        files: dict[str, str],
        *,
        commit_sha: str = "",
        tree_sha: str | None = None,
    ) -> tuple[WorkspaceRemoteSnapshot, list[PullDiagnostic]]:
        diagnostics: list[PullDiagnostic] = []
        manifest = WorkspaceManifest()
        if manifest_content := files.get(MANIFEST_FILENAME):
            try:
                manifest = WorkspaceManifest.model_validate_json(manifest_content)
            except Exception as e:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=MANIFEST_FILENAME,
                        workflow_title=None,
                        error_type="parse",
                        message=f"Invalid workspace manifest: {str(e)}",
                        details={"error": str(e)},
                    )
                )
                return (
                    WorkspaceRemoteSnapshot(
                        commit_sha=commit_sha,
                        tree_sha=tree_sha,
                        files=files,
                        spec=WorkspaceSpec(),
                        spec_hash=stable_hash(WorkspaceSpec()),
                    ),
                    diagnostics,
                )

        workflow_root = manifest.resources.workflows.strip("/")
        workflows: dict[str, WorkflowResourceSpec] = {}
        for path, content in sorted(files.items()):
            if not is_workflow_definition_path(path, workflow_root=workflow_root):
                continue
            spec, diagnostic = parse_workflow_spec(path, content)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
                continue
            if spec is not None:
                workflows[spec.id] = spec

        spec = WorkspaceSpec(workflows=dict(sorted(workflows.items())))
        return (
            WorkspaceRemoteSnapshot(
                commit_sha=commit_sha,
                tree_sha=tree_sha,
                files=files,
                spec=spec,
                spec_hash=stable_hash(spec),
            ),
            diagnostics,
        )

    async def pull(
        self,
        *,
        url: GitUrl,
        options: PullOptions,
    ) -> PullResult:
        if not options.commit_sha:
            return PullResult(
                success=False,
                commit_sha="",
                workflows_found=0,
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="validation",
                        message="commit_sha is required",
                        details={},
                    )
                ],
                message="commit_sha is required",
            )

        git_svc = WorkspaceGitHubSyncService(session=self.session, role=self.role)
        remote_tree = await git_svc.read_files(url=url, ref=options.commit_sha)
        snapshot, diagnostics = await self.parse_files(
            remote_tree.files,
            commit_sha=remote_tree.commit_sha,
            tree_sha=remote_tree.tree_sha,
        )
        if diagnostics:
            return PullResult(
                success=False,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(snapshot.spec.workflows),
                workflows_imported=0,
                diagnostics=diagnostics,
                message=f"Failed to parse {len(diagnostics)} workflow definition(s)",
            )

        if options.dry_run:
            return PullResult(
                success=True,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(snapshot.spec.workflows),
                workflows_imported=0,
                diagnostics=[],
                message="Dry run completed - workspace spec validated but not applied",
            )

        local_projection = await self.project_workspace(create_missing_mappings=True)
        state = await self._get_or_create_state(url=url)
        pending = await self._pending_changes_from_projection(
            projection=local_projection,
            state=state,
        )
        plan = self._plan_pull(
            pending=pending,
            local_spec=local_projection.spec,
            remote_spec=snapshot.spec,
            remote_changed=await self._remote_changed_source_ids(snapshot),
        )
        if plan.conflicts:
            return PullResult(
                success=False,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(snapshot.spec.workflows),
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path=workflow_source_path(source_id),
                        workflow_title=None,
                        error_type="conflict",
                        message=(
                            "Resource changed both locally and in the repository "
                            "since the last sync. Export or discard the local "
                            "change before pulling."
                        ),
                        details={"source_id": source_id},
                    )
                    for source_id in plan.conflicts
                ],
                message=f"Pull blocked by {len(plan.conflicts)} conflicting resource(s)",
            )

        spec_to_apply = WorkspaceSpec(
            workflows={
                source_id: workflow_spec
                for source_id, workflow_spec in snapshot.spec.workflows.items()
                if source_id in plan.to_import
            }
        )
        result = await self._reconcile_workflow_specs(
            spec=spec_to_apply,
            commit_sha=snapshot.commit_sha,
        )
        if not result.success:
            return result

        await self._rebaseline_convergent_mappings(
            source_ids=plan.convergent,
            local_spec=local_projection.spec,
            commit_sha=snapshot.commit_sha,
        )
        await self._untrack_remote_deleted_mappings(source_ids=plan.untrack)
        await self._record_successful_pull(
            state=state,
            snapshot=snapshot,
            url=url,
        )
        return PullResult(
            success=True,
            commit_sha=snapshot.commit_sha,
            workflows_found=len(snapshot.spec.workflows),
            workflows_imported=result.workflows_imported,
            diagnostics=[],
            message=(
                f"Imported {result.workflows_imported} changed workflow(s); "
                f"{len(snapshot.spec.workflows) - result.workflows_imported} already up to date"
            ),
        )

    async def get_status(self) -> WorkspaceSyncStatus:
        """Return the workspace/repo three-way sync status for the configured repo."""
        url = await self._workspace_git_url()
        state = await self._get_state(url=url) or self._unsaved_state(url=url)
        local_projection = await self.project_workspace(create_missing_mappings=False)

        remote_snapshot: WorkspaceRemoteSnapshot | None = None
        diagnostics: list[PullDiagnostic] = []
        remote_ref = state.target_ref or url.ref or "main"
        try:
            remote_snapshot, diagnostics = await self._read_remote_snapshot(
                url=url,
                ref=remote_ref,
            )
        except Exception as e:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path="",
                    workflow_title=None,
                    error_type="github",
                    message=f"Failed to read remote workspace spec: {str(e)}",
                    details={"error": str(e), "ref": remote_ref},
                )
            )

        pending = await self._pending_changes_from_projection(
            projection=local_projection,
            state=state,
        )
        plan = self._plan_pull(
            pending=pending,
            local_spec=local_projection.spec,
            remote_spec=remote_snapshot.spec if remote_snapshot else WorkspaceSpec(),
            remote_changed=await self._remote_changed_source_ids(remote_snapshot),
        )
        remote_spec_hash = remote_snapshot.spec_hash if remote_snapshot else None
        status = self._classify_status(
            has_base=state.base_commit_sha is not None,
            has_remote=remote_snapshot is not None,
            local_changed_source_ids=(
                (plan.local_changed | plan.local_deleted)
                - plan.convergent
                - plan.resurrect
            ),
            remote_changed_source_ids=plan.remote_changed,
            remote_diagnostics=diagnostics,
        )

        return WorkspaceSyncStatus(
            status=status,
            base_spec_hash=state.base_spec_hash,
            local_spec_hash=local_projection.spec_hash,
            remote_spec_hash=remote_spec_hash,
            base_commit_sha=state.base_commit_sha,
            remote_commit_sha=remote_snapshot.commit_sha if remote_snapshot else None,
            target_ref=remote_ref,
            pending_change_count=len(pending.changes),
            diagnostics=diagnostics,
        )

    async def list_pending_changes(self) -> WorkspaceSyncPendingChanges:
        """List local syncable changes relative to the last synced base."""
        url = await self._workspace_git_url()
        state = await self._get_state(url=url) or self._unsaved_state(url=url)
        projection = await self.project_workspace(create_missing_mappings=False)
        return await self._pending_changes_from_projection(
            projection=projection,
            state=state,
        )

    async def create_changeset(self, params: ChangeSetCreate) -> ChangeSetRead:
        """Create a reviewable ChangeSet from selected pending resources."""
        if not params.resources:
            raise TracecatValidationError("At least one resource is required")

        projection = await self.project_workspace(create_missing_mappings=True)
        specs = self._select_workflow_specs(
            projection=projection,
            resources=params.resources,
        )
        selected_files = self._files_for_workflow_specs(specs)
        changeset = await self._create_changeset_for_specs(
            title=params.title,
            description=params.description,
            specs=specs,
            selected_files=selected_files,
        )
        await self.session.commit()
        return self._changeset_to_read(changeset)

    async def list_changesets(self, *, limit: int = 50) -> list[ChangeSetRead]:
        stmt = (
            select(WorkspaceSyncChangeSet)
            .where(
                WorkspaceSyncChangeSet.workspace_id == self.workspace_id,
                WorkspaceSyncChangeSet.provider == SyncProvider.GIT.value,
            )
            .order_by(desc(WorkspaceSyncChangeSet.created_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [self._changeset_to_read(row) for row in result.scalars().all()]

    async def get_changeset(self, changeset_id: uuid.UUID) -> ChangeSetRead:
        changeset = await self._get_changeset(changeset_id)
        return self._changeset_to_read(changeset)

    async def export_changeset(
        self,
        *,
        changeset_id: uuid.UUID,
        params: ChangeSetExport,
    ) -> WorkspaceSyncExportResult:
        """Materialize a ChangeSet into a Git branch and optional pull request."""
        changeset = await self._get_changeset(changeset_id)
        selected_files = self._changeset_rendered_files(changeset)
        url = await self._workspace_git_url()

        git_svc = WorkspaceGitHubSyncService(session=self.session, role=self.role)
        commit = await git_svc.write_files(
            url=url,
            files=selected_files,
            message=params.message,
            branch=params.branch,
            create_pr=params.create_pr,
            pr_base_branch=params.pr_base_branch,
        )

        materialization = WorkspaceSyncMaterialization(
            workspace_id=self.workspace_id,
            changeset_id=changeset.id,
            provider=SyncProvider.GIT.value,
            branch=commit.ref,
            base_ref=commit.base_ref,
            pr_number=commit.pr_number,
            pr_url=commit.pr_url,
            commit_shas=[commit.sha] if commit.sha else [],
            status=(
                MaterializationStatus.COMMITTED.value
                if commit.sha
                else MaterializationStatus.NO_OP.value
            ),
        )
        changeset.status = ChangeSetStatus.EXPORTED.value
        self.session.add_all([materialization, changeset])
        await self.session.commit()
        return WorkspaceSyncExportResult(changeset_id=changeset.id, commit=commit)

    async def export_workflow(
        self,
        *,
        workflow: Workflow,
        dsl: DSLInput,
        options: PushOptions,
    ) -> WorkspaceSyncExportResult:
        if not options.branch:
            raise ValueError("branch is required for workspace sync export")

        url = await self._workspace_git_url()
        await self.session.refresh(
            workflow,
            ["tags", "folder", "schedules", "webhook", "case_trigger"],
        )
        preferred_source_id = default_workflow_source_id(
            alias=workflow.alias,
            title=dsl.title,
        )
        mapping = await self._ensure_resource_mapping(
            resource_type=SyncResourceType.WORKFLOW.value,
            local_id=WorkflowUUID.new(workflow.id),
            preferred_source_id=preferred_source_id,
            source_path=workflow_source_path(preferred_source_id),
            create=True,
            reserved_source_ids=set(),
        )
        if mapping is None:
            raise RuntimeError("Expected workflow source mapping to be created")

        workflow_spec = workflow_spec_from_orm(
            workflow,
            dsl=dsl,
            source_id=mapping.source_id,
        )
        manifest = WorkspaceManifest()
        selected_spec = WorkspaceSpec(workflows={workflow_spec.id: workflow_spec})
        selected_files = self._files_from_spec(manifest=manifest, spec=selected_spec)
        changeset = await self._create_changeset_for_specs(
            title=options.message,
            description=None,
            specs=[workflow_spec],
            selected_files=selected_files,
        )

        git_svc = WorkspaceGitHubSyncService(session=self.session, role=self.role)
        commit = await git_svc.write_files(
            url=url,
            files=selected_files,
            message=options.message,
            branch=options.branch,
            create_pr=options.create_pr,
            pr_base_branch=options.pr_base_branch,
        )

        materialization = WorkspaceSyncMaterialization(
            workspace_id=self.workspace_id,
            changeset_id=changeset.id,
            provider=SyncProvider.GIT.value,
            branch=commit.ref,
            base_ref=commit.base_ref,
            pr_number=commit.pr_number,
            pr_url=commit.pr_url,
            commit_shas=[commit.sha] if commit.sha else [],
            status=(
                MaterializationStatus.COMMITTED.value
                if commit.sha
                else MaterializationStatus.NO_OP.value
            ),
        )
        changeset.status = ChangeSetStatus.EXPORTED.value
        # Mapping sync metadata only advances on pull: the export landed on a
        # branch, not the synced base, so the resource is still pending.
        workflow.git_sync_branch = commit.ref
        self.session.add_all([materialization, changeset, workflow])
        await self.session.commit()
        return WorkspaceSyncExportResult(changeset_id=changeset.id, commit=commit)

    async def export_workflow_publish_result(
        self,
        *,
        workflow: Workflow,
        dsl: DSLInput,
        options: PushOptions,
    ) -> WorkflowDslPublishResult:
        result = await self.export_workflow(workflow=workflow, dsl=dsl, options=options)
        return result.as_workflow_publish_result()

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[Any]:
        return await WorkspaceGitHubSyncService(
            session=self.session,
            role=self.role,
        ).list_commits(url=url, branch=branch, limit=limit)

    async def list_branches(self, *, url: GitUrl, limit: int = 100) -> list[Any]:
        return await WorkspaceGitHubSyncService(
            session=self.session,
            role=self.role,
        ).list_branches(url=url, limit=limit)

    async def _list_projectable_workflows(
        self,
        *,
        workflow_ids: Sequence[WorkflowUUID] | None,
    ) -> list[Workflow]:
        stmt = (
            select(Workflow)
            .where(Workflow.workspace_id == self.workspace_id)
            .order_by(Workflow.created_at, Workflow.id)
        )
        if workflow_ids:
            stmt = stmt.where(Workflow.id.in_(list(workflow_ids)))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _get_workflow_dsl(
        self,
        workflow: Workflow,
        *,
        defn_service: WorkflowDefinitionsService,
        mgmt_service: WorkflowsManagementService,
    ) -> DSLInput:
        definition = await defn_service.get_definition_by_workflow_id(
            WorkflowUUID.new(workflow.id)
        )
        if definition and definition.content:
            return DSLInput.model_validate(definition.content)
        return await mgmt_service.build_dsl_from_workflow(workflow)

    async def _ensure_resource_mapping(
        self,
        *,
        resource_type: str,
        local_id: uuid.UUID,
        preferred_source_id: str,
        source_path: str,
        create: bool,
        reserved_source_ids: set[str],
    ) -> WorkspaceSyncResourceMapping | None:
        stmt = select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == SyncProvider.GIT.value,
            WorkspaceSyncResourceMapping.resource_type == resource_type,
            WorkspaceSyncResourceMapping.local_id == local_id,
        )
        if mapping := (await self.session.execute(stmt)).scalar_one_or_none():
            return mapping
        if not create:
            return None

        source_id = await self._unique_source_id(
            resource_type=resource_type,
            preferred_source_id=preferred_source_id,
            reserved_source_ids=reserved_source_ids,
        )
        mapping = WorkspaceSyncResourceMapping(
            workspace_id=self.workspace_id,
            provider=SyncProvider.GIT.value,
            resource_type=resource_type,
            source_id=source_id,
            source_path=workflow_source_path(source_id)
            if resource_type == SyncResourceType.WORKFLOW.value
            else source_path,
            local_id=local_id,
            sync_status=ResourceSyncStatus.UNTRACKED.value,
        )
        self.session.add(mapping)
        await self.session.flush()
        return mapping

    async def _unique_source_id(
        self,
        *,
        resource_type: str,
        preferred_source_id: str,
        reserved_source_ids: set[str],
    ) -> str:
        base = preferred_source_id
        counter = 2
        candidate = base
        while candidate in reserved_source_ids or await self._source_id_exists(
            resource_type=resource_type,
            source_id=candidate,
        ):
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    async def _source_id_exists(self, *, resource_type: str, source_id: str) -> bool:
        stmt = select(WorkspaceSyncResourceMapping.id).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == SyncProvider.GIT.value,
            WorkspaceSyncResourceMapping.resource_type == resource_type,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    def _files_from_spec(
        self,
        *,
        manifest: WorkspaceManifest,
        spec: WorkspaceSpec,
    ) -> dict[str, str]:
        files = {MANIFEST_FILENAME: canonical_json_text(manifest)}
        for source_id, workflow_spec in sorted(spec.workflows.items()):
            files[workflow_source_path(source_id)] = serialize_workflow_spec(
                workflow_spec
            )
        return files

    def _files_for_workflow_specs(
        self,
        specs: list[WorkflowResourceSpec],
    ) -> dict[str, str]:
        manifest = WorkspaceManifest()
        selected_spec = WorkspaceSpec(
            workflows={
                spec.id: spec for spec in sorted(specs, key=lambda item: item.id)
            }
        )
        return self._files_from_spec(manifest=manifest, spec=selected_spec)

    async def _reconcile_workflow_specs(
        self,
        *,
        spec: WorkspaceSpec,
        commit_sha: str,
    ) -> PullResult:
        local_ids: dict[str, WorkflowUUID] = {}
        remote_workflows = []
        for source_id, workflow_spec in sorted(spec.workflows.items()):
            local_id = await self._resolve_local_workflow_id(source_id)
            local_ids[source_id] = local_id
            remote_workflows.append(
                workflow_spec_to_remote(workflow_spec, local_workflow_id=local_id)
            )

        result = await WorkflowImportService(
            session=self.session,
            role=self.role,
        ).import_workflows_atomic(remote_workflows, commit_sha=commit_sha)
        if not result.success:
            return result

        for source_id, workflow_spec in sorted(spec.workflows.items()):
            await self._upsert_remote_mapping(
                source_id=source_id,
                source_path=workflow_source_path(source_id),
                local_id=local_ids[source_id],
                commit_sha=commit_sha,
                spec_hash=stable_hash(workflow_spec),
            )
        await self.session.flush()
        await self._record_projected_hashes(workflow_ids=list(local_ids.values()))
        await self.session.commit()
        return result

    async def _record_projected_hashes(
        self,
        *,
        workflow_ids: list[WorkflowUUID],
    ) -> None:
        """Re-project imported workflows and record their local-space hashes.

        ``last_synced_spec_hash`` lives in repo-spec space while pending-change
        detection compares local projections, so the post-reconcile projection
        is the postcondition check (``P(W_next) == S``) and the baseline for
        future pending-change comparisons.
        """
        if not workflow_ids:
            return
        projection = await self.project_workspace(
            workflow_ids=workflow_ids,
            create_missing_mappings=False,
        )
        mappings_by_source_id = {
            mapping.source_id: mapping for mapping in await self._workflow_mappings()
        }
        for source_id, spec in projection.spec.workflows.items():
            mapping = mappings_by_source_id.get(source_id)
            if mapping is None:
                continue
            projected_hash = stable_hash(spec)
            mapping.last_projected_spec_hash = projected_hash
            if mapping.last_synced_spec_hash != projected_hash:
                self.logger.warning(
                    "Workspace sync postcondition mismatch: local projection "
                    "differs from the pulled spec",
                    source_id=source_id,
                    last_synced_spec_hash=mapping.last_synced_spec_hash,
                    projected_spec_hash=projected_hash,
                )
            self.session.add(mapping)

    async def _rebaseline_convergent_mappings(
        self,
        *,
        source_ids: set[str],
        local_spec: WorkspaceSpec,
        commit_sha: str,
    ) -> None:
        """Advance sync metadata for resources whose local and remote specs already agree."""
        if not source_ids:
            return
        mappings_by_source_id = {
            mapping.source_id: mapping for mapping in await self._workflow_mappings()
        }
        for source_id in sorted(source_ids):
            mapping = mappings_by_source_id.get(source_id)
            if mapping is None:
                continue
            workflow_spec = local_spec.workflows.get(source_id)
            if workflow_spec is None:
                # Deleted on both sides: the mapping no longer identifies anything.
                await self.session.delete(mapping)
                continue
            spec_hash = stable_hash(workflow_spec)
            mapping.last_synced_commit_sha = commit_sha
            mapping.last_synced_spec_hash = spec_hash
            mapping.last_projected_spec_hash = spec_hash
            mapping.sync_status = ResourceSyncStatus.SYNCED.value
            self.session.add(mapping)
        await self.session.flush()

    async def _untrack_remote_deleted_mappings(self, *, source_ids: set[str]) -> None:
        """Detach mappings for resources deleted remotely but kept locally.

        With the ignore-missing delete policy the local resource survives the
        pull; clearing sync metadata turns it back into a pending create so the
        admin can re-export it or delete it explicitly.
        """
        if not source_ids:
            return
        mappings_by_source_id = {
            mapping.source_id: mapping for mapping in await self._workflow_mappings()
        }
        for source_id in sorted(source_ids):
            mapping = mappings_by_source_id.get(source_id)
            if mapping is None:
                continue
            mapping.last_synced_commit_sha = None
            mapping.last_synced_spec_hash = None
            mapping.last_projected_spec_hash = None
            mapping.sync_status = ResourceSyncStatus.UNTRACKED.value
            self.session.add(mapping)
        await self.session.flush()

    async def _resolve_local_workflow_id(self, source_id: str) -> WorkflowUUID:
        stmt = select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == SyncProvider.GIT.value,
            WorkspaceSyncResourceMapping.resource_type
            == SyncResourceType.WORKFLOW.value,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        if mapping := (await self.session.execute(stmt)).scalar_one_or_none():
            return WorkflowUUID.new(mapping.local_id)

        try:
            legacy_id = WorkflowUUID.new(source_id)
        except ValueError:
            return WorkflowUUID.new_uuid4()

        workflow = await self.session.scalar(
            select(Workflow).where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == legacy_id,
            )
        )
        return WorkflowUUID.new(workflow.id) if workflow else WorkflowUUID.new_uuid4()

    async def _upsert_remote_mapping(
        self,
        *,
        source_id: str,
        source_path: str,
        local_id: WorkflowUUID,
        commit_sha: str,
        spec_hash: str,
    ) -> WorkspaceSyncResourceMapping:
        stmt = select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == SyncProvider.GIT.value,
            WorkspaceSyncResourceMapping.resource_type
            == SyncResourceType.WORKFLOW.value,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        mapping = (await self.session.execute(stmt)).scalar_one_or_none()
        if mapping is None:
            mapping = WorkspaceSyncResourceMapping(
                workspace_id=self.workspace_id,
                provider=SyncProvider.GIT.value,
                resource_type=SyncResourceType.WORKFLOW.value,
                source_id=source_id,
                local_id=local_id,
            )
        mapping.source_path = source_path
        mapping.last_synced_commit_sha = commit_sha
        mapping.last_synced_spec_hash = spec_hash
        mapping.sync_status = ResourceSyncStatus.SYNCED.value
        self.session.add(mapping)
        return mapping

    async def _record_successful_pull(
        self,
        *,
        state: WorkspaceSyncState,
        snapshot: WorkspaceRemoteSnapshot,
        url: GitUrl,
    ) -> None:
        state.repo_url = url.to_url()
        state.target_ref = url.ref or state.target_ref
        state.base_commit_sha = snapshot.commit_sha
        state.base_tree_sha = snapshot.tree_sha
        state.base_spec_hash = snapshot.spec_hash
        state.last_remote_commit_sha = snapshot.commit_sha
        state.last_remote_tree_sha = snapshot.tree_sha
        state.status = SyncStateStatus.CLEAN.value
        state.last_direction = SyncDirection.PULL.value
        state.last_synced_at = datetime.now(UTC)
        state.last_error = None
        self.session.add(state)
        await self.session.commit()

    async def _read_remote_snapshot(
        self,
        *,
        url: GitUrl,
        ref: str,
    ) -> tuple[WorkspaceRemoteSnapshot, list[PullDiagnostic]]:
        git_svc = WorkspaceGitHubSyncService(session=self.session, role=self.role)
        remote_tree = await git_svc.read_files(url=url, ref=ref)
        return await self.parse_files(
            remote_tree.files,
            commit_sha=remote_tree.commit_sha,
            tree_sha=remote_tree.tree_sha,
        )

    async def _pending_changes_from_projection(
        self,
        *,
        projection: WorkspaceProjection,
        state: WorkspaceSyncState,
    ) -> WorkspaceSyncPendingChanges:
        mappings = await self._workflow_mappings()
        mappings_by_source_id = {mapping.source_id: mapping for mapping in mappings}
        changes: list[WorkspaceSyncPendingChange] = []

        for source_id, spec in sorted(projection.spec.workflows.items()):
            mapping = mappings_by_source_id.get(source_id)
            before_hash = mapping.last_projected_spec_hash if mapping else None
            after_hash = stable_hash(spec)
            if before_hash == after_hash:
                continue
            operation = (
                SyncOperation.CREATE if before_hash is None else SyncOperation.UPDATE
            )
            changes.append(
                WorkspaceSyncPendingChange(
                    resource_type=SyncResourceType.WORKFLOW.value,
                    source_id=source_id,
                    source_path=workflow_source_path(source_id),
                    local_id=mapping.local_id if mapping else None,
                    operation=operation,
                    title=spec.definition.title,
                    alias=spec.alias,
                    before_spec_hash=before_hash,
                    after_spec_hash=after_hash,
                    exportable=True,
                )
            )

        # Previously synced resources missing from the projection were deleted
        # locally. Deletes are not exportable in v1 but must surface so status
        # and pending counts stay honest.
        projected_source_ids = set(projection.spec.workflows)
        for mapping in mappings:
            if mapping.source_id in projected_source_ids:
                continue
            if mapping.last_projected_spec_hash is None:
                continue
            changes.append(
                WorkspaceSyncPendingChange(
                    resource_type=SyncResourceType.WORKFLOW.value,
                    source_id=mapping.source_id,
                    source_path=mapping.source_path
                    or workflow_source_path(mapping.source_id),
                    local_id=mapping.local_id,
                    operation=SyncOperation.DELETE,
                    title=None,
                    alias=None,
                    before_spec_hash=mapping.last_projected_spec_hash,
                    after_spec_hash=None,
                    exportable=False,
                )
            )
        changes.sort(key=lambda change: change.source_id)

        return WorkspaceSyncPendingChanges(
            base_spec_hash=state.base_spec_hash,
            local_spec_hash=projection.spec_hash,
            changes=changes,
        )

    async def _remote_changed_source_ids(
        self,
        remote_snapshot: WorkspaceRemoteSnapshot | None,
    ) -> set[str]:
        if remote_snapshot is None:
            return set()

        mappings = await self._workflow_mappings()
        mappings_by_source_id = {mapping.source_id: mapping for mapping in mappings}
        remote_source_ids = set(remote_snapshot.spec.workflows)
        changed: set[str] = set()

        for source_id, spec in remote_snapshot.spec.workflows.items():
            mapping = mappings_by_source_id.get(source_id)
            remote_hash = stable_hash(spec)
            if mapping is None or mapping.last_synced_spec_hash != remote_hash:
                changed.add(source_id)

        for mapping in mappings:
            if (
                mapping.last_synced_spec_hash
                and mapping.source_id not in remote_source_ids
            ):
                changed.add(mapping.source_id)

        return changed

    def _classify_status(
        self,
        *,
        has_base: bool,
        has_remote: bool,
        local_changed_source_ids: set[str],
        remote_changed_source_ids: set[str],
        remote_diagnostics: list[PullDiagnostic],
    ) -> SyncStateStatus:
        if remote_diagnostics:
            return SyncStateStatus.ERROR
        if not has_base or not has_remote:
            return SyncStateStatus.NEVER_SYNCED

        if not local_changed_source_ids and not remote_changed_source_ids:
            return SyncStateStatus.CLEAN
        if local_changed_source_ids and not remote_changed_source_ids:
            return SyncStateStatus.LOCAL_DIRTY
        if not local_changed_source_ids and remote_changed_source_ids:
            return SyncStateStatus.REMOTE_AHEAD
        if local_changed_source_ids & remote_changed_source_ids:
            return SyncStateStatus.CONFLICTED
        return SyncStateStatus.DIVERGED

    def _plan_pull(
        self,
        *,
        pending: WorkspaceSyncPendingChanges,
        local_spec: WorkspaceSpec,
        remote_spec: WorkspaceSpec,
        remote_changed: set[str],
    ) -> PullReconciliationPlan:
        """Build the per-resource reconciliation plan shared by pull and status.

        Policy (v1): convergent resources rebaseline; resources deleted locally
        but changed remotely are re-imported (Git owns desired state); resources
        deleted remotely but unchanged locally are untracked; everything changed
        on both sides with differing specs is a conflict.
        """
        local_deleted = {
            change.source_id
            for change in pending.changes
            if change.operation == SyncOperation.DELETE
        }
        local_changed = {change.source_id for change in pending.changes} - local_deleted
        remote_present = set(remote_spec.workflows)
        remote_deleted = remote_changed - remote_present
        overlap = (local_changed | local_deleted) & remote_changed
        convergent = self._convergent_source_ids(
            local_spec=local_spec,
            remote_spec=remote_spec,
            candidates=overlap,
        )
        resurrect = (local_deleted & remote_changed & remote_present) - convergent
        conflicts = sorted(overlap - convergent - resurrect)
        to_import = (remote_changed & remote_present) - convergent
        untrack = remote_deleted - local_deleted
        return PullReconciliationPlan(
            local_changed=local_changed,
            local_deleted=local_deleted,
            remote_changed=remote_changed,
            remote_deleted=remote_deleted,
            convergent=convergent,
            resurrect=resurrect,
            conflicts=conflicts,
            to_import=to_import,
            untrack=untrack,
        )

    def _convergent_source_ids(
        self,
        *,
        local_spec: WorkspaceSpec,
        remote_spec: WorkspaceSpec | None,
        candidates: set[str],
    ) -> set[str]:
        """Source ids changed on both sides where no merge is required.

        Covers identical local/remote specs (the typical exported-then-merged
        change) and resources deleted on both sides. These rebaseline instead
        of conflicting.
        """
        if remote_spec is None or not candidates:
            return set()
        convergent: set[str] = set()
        for source_id in candidates:
            local = local_spec.workflows.get(source_id)
            remote = remote_spec.workflows.get(source_id)
            if local is None and remote is None:
                convergent.add(source_id)
            elif (
                local is not None
                and remote is not None
                and stable_hash(local) == stable_hash(remote)
            ):
                convergent.add(source_id)
        return convergent

    async def _workflow_mappings(self) -> list[WorkspaceSyncResourceMapping]:
        stmt = select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == SyncProvider.GIT.value,
            WorkspaceSyncResourceMapping.resource_type
            == SyncResourceType.WORKFLOW.value,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def _select_workflow_specs(
        self,
        *,
        projection: WorkspaceProjection,
        resources: list[ResourceRef],
    ) -> list[WorkflowResourceSpec]:
        specs: list[WorkflowResourceSpec] = []
        seen_source_ids: set[str] = set()
        for resource in resources:
            if resource.resource_type != SyncResourceType.WORKFLOW.value:
                raise TracecatValidationError(
                    f"Unsupported sync resource type: {resource.resource_type}"
                )
            if resource.source_id in seen_source_ids:
                continue
            spec = projection.spec.workflows.get(resource.source_id)
            if spec is None:
                raise TracecatValidationError(
                    f"Workflow source id not found in local projection: {resource.source_id}"
                )
            specs.append(spec)
            seen_source_ids.add(resource.source_id)
        return specs

    async def _create_changeset_for_specs(
        self,
        *,
        title: str,
        description: str | None,
        specs: list[WorkflowResourceSpec],
        selected_files: dict[str, str],
    ) -> WorkspaceSyncChangeSet:
        state = await self._get_or_create_state(url=await self._workspace_git_url())
        mappings_by_source_id = {
            mapping.source_id: mapping for mapping in await self._workflow_mappings()
        }
        selected_resources = [
            {
                "resource_type": SyncResourceType.WORKFLOW.value,
                "source_id": spec.id,
                "source_path": workflow_source_path(spec.id),
                "local_id": str(mappings_by_source_id[spec.id].local_id)
                if spec.id in mappings_by_source_id
                else None,
            }
            for spec in specs
        ]
        changeset = WorkspaceSyncChangeSet(
            workspace_id=self.workspace_id,
            provider=SyncProvider.GIT.value,
            title=title,
            description=description,
            base_commit_sha=state.base_commit_sha,
            base_spec_hash=state.base_spec_hash,
            selected_resources=selected_resources,
            selected_paths=sorted(selected_files),
            rendered_files=dict(sorted(selected_files.items())),
            validation_status=ValidationStatus.VALID.value,
            validation_result={},
            status=ChangeSetStatus.VALIDATED.value,
            created_by=self.role.user_id,
        )
        self.session.add(changeset)
        await self.session.flush()
        for spec in specs:
            mapping = mappings_by_source_id.get(spec.id)
            item = WorkspaceSyncChangeSetItem(
                workspace_id=self.workspace_id,
                changeset_id=changeset.id,
                resource_type=SyncResourceType.WORKFLOW.value,
                source_id=spec.id,
                source_path=workflow_source_path(spec.id),
                local_id=mapping.local_id if mapping else None,
                operation=(
                    SyncOperation.CREATE.value
                    if mapping is None or mapping.last_projected_spec_hash is None
                    else SyncOperation.UPDATE.value
                ),
                spec_hash=stable_hash(spec),
                dependencies=[],
            )
            self.session.add(item)
        await self.session.flush()
        return changeset

    def _changeset_rendered_files(
        self,
        changeset: WorkspaceSyncChangeSet,
    ) -> dict[str, str]:
        rendered_files = changeset.rendered_files or {}
        if not rendered_files:
            raise TracecatValidationError(
                "Workspace sync ChangeSet has no frozen files. Recreate the ChangeSet before exporting."
            )
        if any(
            not isinstance(path, str) or not isinstance(content, str)
            for path, content in rendered_files.items()
        ):
            raise TracecatValidationError(
                "Workspace sync ChangeSet frozen files are invalid."
            )
        return dict(sorted(rendered_files.items()))

    async def _get_changeset(
        self,
        changeset_id: uuid.UUID,
    ) -> WorkspaceSyncChangeSet:
        stmt = select(WorkspaceSyncChangeSet).where(
            WorkspaceSyncChangeSet.workspace_id == self.workspace_id,
            WorkspaceSyncChangeSet.provider == SyncProvider.GIT.value,
            WorkspaceSyncChangeSet.id == changeset_id,
        )
        changeset = (await self.session.execute(stmt)).scalar_one_or_none()
        if changeset is None:
            raise TracecatNotFoundError("Workspace sync ChangeSet not found")
        return changeset

    def _changeset_to_read(self, changeset: WorkspaceSyncChangeSet) -> ChangeSetRead:
        return ChangeSetRead(
            id=changeset.id,
            title=changeset.title,
            description=changeset.description,
            base_commit_sha=changeset.base_commit_sha,
            base_spec_hash=changeset.base_spec_hash,
            selected_resources=changeset.selected_resources,
            selected_paths=changeset.selected_paths,
            validation_status=changeset.validation_status,
            validation_result=changeset.validation_result,
            status=changeset.status,
        )

    async def _get_or_create_state(self, *, url: GitUrl) -> WorkspaceSyncState:
        if state := await self._get_state(url=url):
            return state
        repo_url = url.to_url()
        target_ref = url.ref or "main"
        insert_stmt = (
            insert(WorkspaceSyncState)
            .values(
                workspace_id=self.workspace_id,
                provider=SyncProvider.GIT.value,
                repo_url=repo_url,
                target_ref=target_ref,
                status=SyncStateStatus.NEVER_SYNCED.value,
            )
            .on_conflict_do_nothing(
                constraint="uq_workspace_sync_state_workspace_provider_repo_ref"
            )
        )
        await self.session.execute(insert_stmt)
        state = await self._get_state(url=url)
        if state is None:
            raise RuntimeError("Workspace sync state was not created")
        return state

    async def _get_state(self, *, url: GitUrl) -> WorkspaceSyncState | None:
        repo_url = url.to_url()
        target_ref = url.ref or "main"
        stmt = select(WorkspaceSyncState).where(
            WorkspaceSyncState.workspace_id == self.workspace_id,
            WorkspaceSyncState.provider == SyncProvider.GIT.value,
            WorkspaceSyncState.repo_url == repo_url,
            WorkspaceSyncState.target_ref == target_ref,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def _unsaved_state(self, *, url: GitUrl) -> WorkspaceSyncState:
        return WorkspaceSyncState(
            workspace_id=self.workspace_id,
            provider=SyncProvider.GIT.value,
            repo_url=url.to_url(),
            target_ref=url.ref or "main",
            status=SyncStateStatus.NEVER_SYNCED.value,
        )

    async def _workspace_git_url(self) -> GitUrl:
        workspace = await self._workspace()
        repo_url = (
            workspace.settings.get("git_repo_url") if workspace.settings else None
        )
        if not repo_url:
            raise TracecatSettingsError(
                "Git repository URL not configured for this workspace."
            )
        try:
            return parse_git_url(repo_url, allowed_domains={"github.com"})
        except ValueError as e:
            raise TracecatSettingsError(
                f"Invalid Git repository URL configured for this workspace: {e}"
            ) from e

    async def _workspace(self) -> Workspace:
        workspace = await WorkspaceService(
            session=self.session,
            role=self.role,
        ).get_workspace(self.workspace_id)
        if workspace is None:
            raise TracecatNotFoundError("Workspace not found")
        return workspace
