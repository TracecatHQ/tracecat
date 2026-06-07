"""Workspace Git sync projection, reconciliation, and ChangeSet service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

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
from tracecat.exceptions import TracecatNotFoundError, TracecatSettingsError
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
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportResult,
)
from tracecat.workspace_sync.serialization import (
    canonical_json_text,
    stable_hash,
)
from tracecat.workspace_sync.workflow import (
    default_workflow_source_id,
    parse_workflow_spec,
    serialize_workflow_spec,
    workflow_source_path,
    workflow_spec_from_orm,
    workflow_spec_to_remote,
)
from tracecat.workspaces.service import WorkspaceService


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
            source_id = mapping.source_id if mapping else preferred_source_id
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
            if not path.startswith(f"{workflow_root}/"):
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
        if state.base_spec_hash and local_projection.spec_hash != state.base_spec_hash:
            return PullResult(
                success=False,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(snapshot.spec.workflows),
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="conflict",
                        message=(
                            "Local syncable workspace state changed since the last "
                            "synced base. Export or discard local changes before pulling."
                        ),
                        details={
                            "base_spec_hash": state.base_spec_hash,
                            "local_spec_hash": local_projection.spec_hash,
                        },
                    )
                ],
                message="Pull blocked by local workspace drift",
            )

        result = await self._reconcile_workflow_specs(
            spec=snapshot.spec,
            commit_sha=snapshot.commit_sha,
        )
        if result.success:
            await self._record_successful_pull(
                state=state,
                snapshot=snapshot,
                url=url,
            )
        return result

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
            organization_id=self.organization_id,
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
        mapping.source_path = workflow_source_path(workflow_spec.id)
        mapping.last_synced_commit_sha = commit.sha
        mapping.last_synced_spec_hash = stable_hash(workflow_spec)
        mapping.sync_status = ResourceSyncStatus.SYNCED.value
        workflow.git_sync_branch = commit.ref
        self.session.add_all([materialization, changeset, mapping, workflow])
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
            organization_id=self.organization_id,
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
        await self.session.commit()
        return result

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
                organization_id=self.organization_id,
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

    async def _create_changeset_for_specs(
        self,
        *,
        title: str,
        description: str | None,
        specs: list[WorkflowResourceSpec],
        selected_files: dict[str, str],
    ) -> WorkspaceSyncChangeSet:
        state = await self._get_or_create_state(url=await self._workspace_git_url())
        selected_resources = [
            {
                "resource_type": SyncResourceType.WORKFLOW.value,
                "source_id": spec.id,
                "source_path": workflow_source_path(spec.id),
            }
            for spec in specs
        ]
        changeset = WorkspaceSyncChangeSet(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            provider=SyncProvider.GIT.value,
            title=title,
            description=description,
            base_commit_sha=state.base_commit_sha,
            base_spec_hash=state.base_spec_hash,
            selected_resources=selected_resources,
            selected_paths=sorted(selected_files),
            validation_status=ValidationStatus.VALID.value,
            validation_result={},
            status=ChangeSetStatus.VALIDATED.value,
            created_by=self.role.user_id,
        )
        self.session.add(changeset)
        await self.session.flush()
        for spec in specs:
            item = WorkspaceSyncChangeSetItem(
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
                changeset_id=changeset.id,
                resource_type=SyncResourceType.WORKFLOW.value,
                source_id=spec.id,
                source_path=workflow_source_path(spec.id),
                local_id=None,
                operation=SyncOperation.UPDATE.value,
                spec_hash=stable_hash(spec),
                dependencies=[],
            )
            self.session.add(item)
        await self.session.flush()
        return changeset

    async def _get_or_create_state(self, *, url: GitUrl) -> WorkspaceSyncState:
        repo_url = url.to_url()
        target_ref = url.ref or "main"
        stmt = select(WorkspaceSyncState).where(
            WorkspaceSyncState.workspace_id == self.workspace_id,
            WorkspaceSyncState.provider == SyncProvider.GIT.value,
            WorkspaceSyncState.repo_url == repo_url,
            WorkspaceSyncState.target_ref == target_ref,
        )
        if state := (await self.session.execute(stmt)).scalar_one_or_none():
            return state
        state = WorkspaceSyncState(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            provider=SyncProvider.GIT.value,
            repo_url=repo_url,
            target_ref=target_ref,
            status=SyncStateStatus.NEVER_SYNCED.value,
        )
        self.session.add(state)
        await self.session.flush()
        return state

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
