"""Simple workspace import/export service for VCS-backed specs."""

from __future__ import annotations

import re
import uuid
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import replace
from difflib import unified_diff
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.controls import get_missing_scopes
from tracecat.db.models import Workflow, Workspace, WorkspaceSyncResourceMapping
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.git.utils import parse_git_url
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.service import BaseWorkspaceService
from tracecat.sync import (
    PullDiagnostic,
    PullOptions,
    PullResourceDiff,
    PullResult,
    ResourcePullCount,
)
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import (
    validate_short_branch_name,
)
from tracecat.workspace_sync.adapters import (
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    WORKFLOW_RESOURCE_ADAPTER,
    ResourceAdapter,
    workspace_spec_from_maps,
)
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.importer import (
    ImportedResource,
    WorkspaceResourceImportService,
)
from tracecat.workspace_sync.projector import (
    ProjectedResource,
    WorkspaceResourceProjector,
)
from tracecat.workspace_sync.resources import (
    parse_workspace_spec_files,
    serialize_workspace_spec_files,
    workflow_references,
)
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    ResourceRef,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceManifestResources,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportPreview,
    WorkspaceSyncExportPreviewRequest,
    WorkspaceSyncExportRequest,
    WorkspaceSyncExportResult,
    manifest_resource_roots,
    workspace_manifest_from_json,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.transport import (
    VcsSyncTransport,
    VcsTransportFactory,
    vcs_transport_for_provider,
)
from tracecat.workspace_sync.workflow import (
    workflow_source_path,
    workflow_spec_from_orm,
    workflow_spec_to_remote,
)
from tracecat.workspaces.service import WorkspaceService

MAX_PULL_RESOURCE_DIFF_LINES = 240
"""Maximum number of unified-diff lines kept per resource in a pull preview."""

_EXPORT_READ_SCOPES_BY_RESOURCE_TYPE: dict[SyncResourceType, str] = {
    SyncResourceType.AGENT_PRESET: "agent:read",
    SyncResourceType.SKILL: "agent:read",
    SyncResourceType.TABLE: "table:read",
    SyncResourceType.CASE_TAG: "case:read",
    SyncResourceType.CASE_FIELD: "case:read",
    SyncResourceType.CASE_DROPDOWN: "case:read",
    SyncResourceType.CASE_DURATION: "case:read",
    SyncResourceType.VARIABLE: "variable:read",
    SyncResourceType.SECRET_METADATA: "secret:read",
}
"""Read scope each non-workflow resource type requires before it can be exported."""


class WorkspaceSyncService(BaseWorkspaceService):
    """Direct workspace import/export over a VCS provider."""

    service_name = "workspace_sync"

    def __init__(
        self,
        session: AsyncSession,
        role: Role | None = None,
        *,
        transport_factory: VcsTransportFactory | None = None,
    ) -> None:
        """Initialize the service, optionally with a custom VCS transport factory.

        ``transport_factory`` overrides :func:`vcs_transport_for_provider`, which
        lets tests substitute an in-memory transport.
        """
        super().__init__(session=session, role=role)
        self._transport_factory = transport_factory

    async def export_workspace(
        self,
        params: WorkspaceSyncExportRequest,
    ) -> WorkspaceSyncExportResult:
        """Export selected or all syncable resources to a branch and optional PR."""
        self._validate_export_params(params)
        url = await self._workspace_git_url(provider=params.provider)
        resource_ids = await self._local_ids_from_resource_refs(params.resources)
        projection = await self.project_workspace(
            resource_ids=resource_ids,
            include_schedules=params.include_schedules,
            create_missing_mappings=True,
        )
        self._require_projected_export_scopes(projection.spec)
        transport = self._transport_for_provider(
            params.provider,
        )
        commit = await transport.write_files(
            url=url,
            files=projection.files,
            message=params.message,
            branch=params.branch,
            create_pr=params.create_pr,
            pr_base_branch=params.pr_base_branch,
            delete_missing_paths_under=(
                manifest_resource_roots(projection.manifest)
                if resource_ids is None
                else ()
            ),
        )
        await self.session.commit()
        return WorkspaceSyncExportResult(
            commit=commit,
            files=sorted(projection.files),
        )

    async def preview_export_workspace(
        self,
        params: WorkspaceSyncExportPreviewRequest,
    ) -> WorkspaceSyncExportPreview:
        """Project what an export would push without writing to Git.

        Resolves the same resource selection as ``export_workspace`` and runs
        the projection read-only (``create_missing_mappings=False``) so the
        preview never mutates sync mappings. The returned counts include any
        resources pulled in transitively by the dependency closure.
        """
        resource_ids = await self._local_ids_from_resource_refs(params.resources)
        projection = await self.project_workspace(
            resource_ids=resource_ids,
            include_schedules=params.include_schedules,
            create_missing_mappings=False,
        )
        self._require_projected_export_scopes(projection.spec)
        return WorkspaceSyncExportPreview(
            resource_counts=projection.spec.resource_count_map(),
            files=sorted(projection.files),
        )

    async def export_workflow(
        self,
        *,
        workflow: Workflow,
        dsl: DSLInput,
        params: WorkspaceSyncExportRequest,
    ) -> WorkspaceSyncExportResult:
        """Export a single workflow to a branch and optional PR.

        Unlike :meth:`export_workspace`, this writes only the one workflow's
        files and records the resulting branch on ``workflow.git_sync_branch``.
        """
        self._validate_export_params(params)
        url = await self._workspace_git_url(provider=params.provider)
        await self.session.refresh(
            workflow,
            ["tags", "folder", "schedules", "webhook", "case_trigger"],
        )
        source_id = await self._source_id_for_workflow(
            workflow=workflow,
            create=True,
            reserved_source_ids=set(),
        )
        spec = workflow_spec_from_orm(
            workflow,
            dsl=dsl,
            source_id=source_id,
            include_schedules=params.include_schedules,
        )
        files = self._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(workflows={spec.id: spec}),
        )
        transport = self._transport_for_provider(
            params.provider,
        )
        commit = await transport.write_files(
            url=url,
            files=files,
            message=params.message,
            branch=params.branch,
            create_pr=params.create_pr,
            pr_base_branch=params.pr_base_branch,
        )
        workflow.git_sync_branch = commit.ref
        self.session.add(workflow)
        await self.session.commit()
        return WorkspaceSyncExportResult(commit=commit, files=sorted(files))

    async def pull(
        self,
        *,
        options: PullOptions,
        provider: VcsProvider = VcsProvider.GITHUB,
        sync_schedules: bool = False,
    ) -> PullResult:
        """Import a workspace spec from the configured repository.

        Schedules are intentionally opt-in so imports do not mutate or activate
        environment-specific schedule configuration unless an admin asks for it.
        """
        # A pull always targets an explicit commit so the import is reproducible.
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

        # Read the repository tree at that commit and parse it into a spec.
        url = await self._workspace_git_url(provider=provider)
        transport = self._transport_for_provider(
            provider,
        )
        remote_tree = await transport.read_files(url=url, ref=options.commit_sha)
        snapshot, diagnostics = await self.parse_files(
            remote_tree.files,
            commit_sha=remote_tree.commit_sha,
            tree_sha=remote_tree.tree_sha,
        )
        resource_counts = self._resource_counts_from_spec(snapshot.spec)
        # Parse/manifest problems abort before we touch the database.
        if diagnostics:
            return PullResult(
                success=False,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(snapshot.spec.workflows),
                workflows_imported=0,
                diagnostics=diagnostics,
                message=f"Failed to validate workspace spec: {len(diagnostics)} issue(s)",
                resource_counts=resource_counts,
            )
        # A dry run previews the diff and validates workflows but never writes.
        if options.dry_run:
            resource_diffs = await self._resource_diffs_for_pull(
                snapshot,
                sync_schedules=sync_schedules,
            )
            workflow_diagnostics = await self._validate_workflow_import(snapshot)
            if workflow_diagnostics:
                return PullResult(
                    success=False,
                    commit_sha=snapshot.commit_sha,
                    workflows_found=len(snapshot.spec.workflows),
                    workflows_imported=0,
                    diagnostics=workflow_diagnostics,
                    message=(
                        f"Import failed: {len(workflow_diagnostics)} validation "
                        "error(s) found"
                    ),
                    resource_counts=resource_counts,
                    resource_diffs=resource_diffs,
                )
            return PullResult(
                success=True,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(snapshot.spec.workflows),
                workflows_imported=0,
                diagnostics=[],
                message=(
                    "Dry run completed - "
                    f"{len(resource_diffs)} resource change(s) detected"
                ),
                resource_counts=resource_counts,
                resource_diffs=resource_diffs,
            )
        # Real pull: reconcile the snapshot into the database.
        return await self._import_snapshot(
            snapshot,
            sync_schedules=sync_schedules,
        )

    async def project_workspace(
        self,
        *,
        workflow_ids: Sequence[WorkflowUUID] | None = None,
        resource_ids: dict[SyncResourceType, set[uuid.UUID]] | None = None,
        include_schedules: bool = False,
        create_missing_mappings: bool = True,
    ) -> WorkspaceProjection:
        """Project the workspace into a manifest, spec, and serialized files.

        Walks the projectable workflow closure (resolving child workflows
        referenced by alias or id), projects non-workflow resources via
        :class:`WorkspaceResourceProjector`, and trims the result to the
        requested selection. When ``create_missing_mappings`` is set, sync
        mappings are minted for any newly projected resource; pass ``False`` for
        read-only previews. A ``None`` selection exports the whole workspace.
        """
        # Normalize the two selection inputs into one per-type map (None = all).
        selection = _selection_from_workflow_ids(
            workflow_ids=workflow_ids,
            resource_ids=resource_ids,
        )
        full_workspace_export = selection is None
        # Resolve the workflows to project, including child workflows they call.
        workflows = await self._projectable_workflow_closure(selection)
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        mgmt_service = WorkflowsManagementService(session=self.session, role=self.role)
        specs: dict[str, WorkflowResourceSpec] = {}

        # Project each workflow to a spec keyed by its (stable) source id.
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
            source_id = await self._source_id_for_workflow(
                workflow=workflow,
                create=create_missing_mappings,
                reserved_source_ids=set(specs),
            )
            specs[source_id] = workflow_spec_from_orm(
                workflow,
                dsl=dsl,
                source_id=source_id,
                include_schedules=include_schedules,
            )

        # Project every non-workflow resource, then trim to the selection's
        # dependency closure (a full export keeps all of them).
        non_workflow_projection = await WorkspaceResourceProjector(
            session=self.session,
            role=self.role,
        ).project_non_workflow_resources(
            resource_types=_non_workflow_resource_types_for_projection(selection)
        )
        non_workflow_spec, projected_resources = self._filtered_non_workflow_spec(
            full_workspace_export=full_workspace_export,
            workflow_specs=specs,
            resource_ids=selection,
            projected_resources=non_workflow_projection.resources,
            projected_spec=non_workflow_projection.spec,
        )
        # Mint sync mappings for the surviving resources (skipped for previews).
        if create_missing_mappings:
            for resource in projected_resources:
                await self._upsert_mapping(
                    resource_type=resource.resource_type.value,
                    source_id=resource.source_id,
                    source_path=resource.source_path,
                    local_id=resource.local_id,
                )

        # Recombine workflow and non-workflow specs and serialize to files.
        manifest = WorkspaceManifest()
        spec = workspace_spec_from_maps(
            {
                WORKFLOW_RESOURCE_ADAPTER.spec_attr: specs,
                **{
                    adapter.spec_attr: adapter.specs(non_workflow_spec)
                    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS
                },
            }
        )
        return WorkspaceProjection(
            manifest=manifest,
            spec=spec,
            files=self._files_from_spec(manifest=manifest, spec=spec),
        )

    async def parse_files(
        self,
        files: dict[str, str],
        *,
        commit_sha: str = "",
        tree_sha: str | None = None,
    ) -> tuple[WorkspaceRemoteSnapshot, list[PullDiagnostic]]:
        """Parse repository files into a remote snapshot and any diagnostics.

        Reads the manifest first; a malformed manifest short-circuits with an
        empty spec and a single parse diagnostic. Otherwise the manifest's roots
        drive :func:`parse_workspace_spec_files`, and its diagnostics are merged
        into the returned list.
        """
        diagnostics: list[PullDiagnostic] = []
        manifest = WorkspaceManifest()
        if manifest_content := files.get(MANIFEST_FILENAME):
            try:
                manifest = workspace_manifest_from_json(manifest_content)
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
                    ),
                    diagnostics,
                )

        spec, resource_diagnostics = parse_workspace_spec_files(
            files,
            manifest=manifest,
        )
        diagnostics.extend(resource_diagnostics)
        return (
            WorkspaceRemoteSnapshot(
                commit_sha=commit_sha,
                tree_sha=tree_sha,
                files=files,
                spec=spec,
            ),
            diagnostics,
        )

    async def list_commits(
        self,
        *,
        branch: str = "main",
        limit: int = 10,
        provider: VcsProvider = VcsProvider.GITHUB,
    ) -> list[GitCommitInfo]:
        """List recent commits on ``branch`` for the workspace repository."""
        url = await self._workspace_git_url(provider=provider)
        transport = self._transport_for_provider(provider)
        return await transport.list_commits(url=url, branch=branch, limit=limit)

    async def list_branches(
        self,
        *,
        limit: int = 100,
        provider: VcsProvider = VcsProvider.GITHUB,
    ) -> list[GitBranchInfo]:
        """List branches for the workspace repository."""
        url = await self._workspace_git_url(provider=provider)
        transport = self._transport_for_provider(provider)
        return await transport.list_branches(url=url, limit=limit)

    def _transport_for_provider(self, provider: VcsProvider) -> VcsSyncTransport:
        """Build the VCS transport for ``provider`` using the configured factory."""
        factory = self._transport_factory or vcs_transport_for_provider
        return factory(
            provider,
            session=self.session,
            role=self.role,
        )

    def _validate_export_params(self, params: WorkspaceSyncExportRequest) -> None:
        """Validate the export branch names, raising :class:`TracecatValidationError`."""
        try:
            validate_short_branch_name(params.branch, field_name="branch")
            if params.pr_base_branch is not None:
                validate_short_branch_name(
                    params.pr_base_branch,
                    field_name="pr_base_branch",
                )
        except ValueError as e:
            raise TracecatValidationError(str(e)) from e

    def _require_projected_export_scopes(self, spec: WorkspaceSpec) -> None:
        """Enforce the read scopes the projected ``spec`` requires.

        Raises :class:`ScopeDeniedError` when the caller's role is missing any
        scope implied by the resource types present in ``spec``.
        """
        required_scopes = sorted(_export_read_scopes_for_spec(spec))
        if not required_scopes:
            return

        if self.role is None or self.role.scopes is None:
            raise ScopeDeniedError(
                required_scopes=required_scopes,
                missing_scopes=required_scopes,
            )

        missing = sorted(get_missing_scopes(self.role.scopes, set(required_scopes)))
        if missing:
            raise ScopeDeniedError(
                required_scopes=required_scopes,
                missing_scopes=missing,
            )

    async def _import_snapshot(
        self,
        snapshot: WorkspaceRemoteSnapshot,
        *,
        sync_schedules: bool,
    ) -> PullResult:
        """Reconcile a validated snapshot into the database within one transaction.

        Validates the workflows, then imports non-workflow resources and
        workflows inside a nested transaction and upserts every sync mapping. Any
        failure rolls the transaction back and surfaces a transaction
        diagnostic. The returned :class:`PullResult` reports found and imported
        counts per resource type.
        """
        # Map every remote source id to a local workflow UUID up front so that
        # child-workflow references can be rewritten to local ids in one pass.
        local_ids: dict[str, WorkflowUUID] = {}
        for source_id in sorted(snapshot.spec.workflows):
            local_id = await self._resolve_local_workflow_id(source_id)
            local_ids[source_id] = local_id
        remote_workflows = []
        for source_id, workflow_spec in sorted(snapshot.spec.workflows.items()):
            local_id = local_ids[source_id]
            remote_workflows.append(
                workflow_spec_to_remote(
                    workflow_spec,
                    local_workflow_id=local_id,
                    local_workflow_ids=local_ids,
                )
            )

        # Validate before writing anything; bail out on the first set of errors.
        workflow_importer = WorkflowImportService(
            session=self.session,
            role=self.role,
        )
        workflow_diagnostics = await workflow_importer.validate_workflows(
            remote_workflows
        )
        if workflow_diagnostics:
            return PullResult(
                success=False,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(remote_workflows),
                workflows_imported=0,
                diagnostics=workflow_diagnostics,
                message=(
                    f"Import failed: {len(workflow_diagnostics)} validation "
                    "error(s) found"
                ),
                resource_counts=self._resource_counts_from_spec(snapshot.spec),
            )

        has_non_workflow_resources = self._has_non_workflow_resources(snapshot.spec)
        if not remote_workflows and not has_non_workflow_resources:
            return PullResult(
                success=True,
                commit_sha=snapshot.commit_sha,
                workflows_found=0,
                workflows_imported=0,
                diagnostics=[],
                message="No workflows found to import",
                resource_counts=self._resource_counts_from_spec(snapshot.spec),
            )

        # Reconcile everything inside one nested transaction: non-workflow
        # resources first (workflows may reference them), then workflows, then
        # refresh the sync mappings. Any failure rolls the whole batch back.
        imported_resources: list[ImportedResource] = []
        try:
            async with self.session.begin_nested():
                if has_non_workflow_resources:
                    imported_resources = await WorkspaceResourceImportService(
                        session=self.session,
                        role=self.role,
                    ).import_non_workflow_resources(snapshot.spec)
                await workflow_importer.import_workflows(
                    remote_workflows,
                    sync_schedules=sync_schedules,
                )
                for source_id in sorted(snapshot.spec.workflows):
                    await self._upsert_mapping(
                        resource_type=SyncResourceType.WORKFLOW.value,
                        source_id=source_id,
                        source_path=workflow_source_path(source_id),
                        local_id=local_ids[source_id],
                    )
                for imported in imported_resources:
                    await self._upsert_mapping(
                        resource_type=imported.resource_type.value,
                        source_id=imported.source_id,
                        source_path=imported.source_path,
                        local_id=imported.local_id,
                    )
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            return PullResult(
                success=False,
                commit_sha=snapshot.commit_sha,
                workflows_found=len(remote_workflows),
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="transaction",
                        message=f"Workspace import transaction failed: {str(e)}",
                        details={"exception": str(e)},
                    )
                ],
                message="Workspace import transaction failed",
                resource_counts=self._resource_counts_from_spec(snapshot.spec),
            )

        return PullResult(
            success=True,
            commit_sha=snapshot.commit_sha,
            workflows_found=len(remote_workflows),
            workflows_imported=len(remote_workflows),
            diagnostics=[],
            message=(
                "Successfully imported workspace resources"
                if imported_resources
                else f"Successfully imported {len(remote_workflows)} workflows"
            ),
            resource_counts=self._resource_counts_from_imported(
                snapshot.spec,
                imported_resources,
                imported_workflows=len(remote_workflows),
            ),
        )

    async def _resource_diffs_for_pull(
        self,
        snapshot: WorkspaceRemoteSnapshot,
        *,
        sync_schedules: bool,
    ) -> list[PullResourceDiff]:
        """Compute per-resource diffs between local state and the incoming snapshot.

        Projects the current workspace read-only and serializes the target spec,
        then emits a canonical unified file diff for every changed path that maps
        to a resource. Adapters own path mapping and labels; the service compares
        the full projection against the repository snapshot.
        """
        current_projection = await self.project_workspace(
            include_schedules=sync_schedules,
            create_missing_mappings=False,
        )
        target_spec = _spec_for_pull_options(
            snapshot.spec,
            sync_schedules=sync_schedules,
        )
        target_files = self._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=target_spec,
        )
        roots = WorkspaceManifest().resources
        diffs: list[PullResourceDiff] = []
        # The preview is a canonical file diff. Adapters own path mapping,
        # serialization, and labels; the service owns comparing the full
        # current projection against the incoming repository snapshot.
        for path, target_content in sorted(target_files.items()):
            identity = _resource_identity_from_path(path, roots=roots)
            if identity is None:
                continue

            current_content = current_projection.files.get(path)
            if current_content == target_content:
                continue

            adapter, source_id = identity
            diff, truncated = _unified_resource_diff(
                path=path,
                before=current_content,
                after=target_content,
            )
            diffs.append(
                PullResourceDiff(
                    resource_type=adapter.resource_type.value,
                    source_id=source_id,
                    source_path=path,
                    change_type="added" if current_content is None else "modified",
                    title=_resource_title(
                        target_spec,
                        adapter=adapter,
                        source_id=source_id,
                    ),
                    diff=diff,
                    truncated=truncated,
                )
            )
        return diffs

    async def _validate_workflow_import(
        self,
        snapshot: WorkspaceRemoteSnapshot,
    ) -> list[PullDiagnostic]:
        """Validate the snapshot's workflows without importing them.

        Used during dry runs to surface workflow validation errors alongside the
        computed resource diffs.
        """
        local_ids: dict[str, WorkflowUUID] = {}
        for source_id in sorted(snapshot.spec.workflows):
            local_id = await self._resolve_local_workflow_id(source_id)
            local_ids[source_id] = local_id
        remote_workflows = []
        for source_id, workflow_spec in sorted(snapshot.spec.workflows.items()):
            local_id = local_ids[source_id]
            remote_workflows.append(
                workflow_spec_to_remote(
                    workflow_spec,
                    local_workflow_id=local_id,
                    local_workflow_ids=local_ids,
                )
            )

        workflow_importer = WorkflowImportService(
            session=self.session,
            role=self.role,
        )
        return await workflow_importer.validate_workflows(remote_workflows)

    async def _list_projectable_workflows(
        self,
        *,
        workflow_ids: Sequence[WorkflowUUID] | None,
    ) -> list[Workflow]:
        """Load the workspace's workflows, optionally filtered to ``workflow_ids``.

        Ordered by creation time then id for a stable projection.
        """
        stmt = (
            select(Workflow)
            .where(Workflow.workspace_id == self.workspace_id)
            .order_by(Workflow.created_at, Workflow.id)
        )
        if workflow_ids:
            stmt = stmt.where(Workflow.id.in_(list(workflow_ids)))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _projectable_workflow_closure(
        self,
        selection: dict[SyncResourceType, set[uuid.UUID]] | None,
    ) -> list[Workflow]:
        """Resolve the workflows to project, expanding child-workflow references.

        A ``None`` selection projects every workflow. Otherwise the selected
        workflows are expanded transitively over their ``execute`` references
        (by alias or id) so child workflows are pulled into the export closure.
        The result preserves the global creation order.
        """
        if selection is None:
            return await self._list_projectable_workflows(workflow_ids=None)

        # A selection without a workflow entry exports no workflows at all.
        if SyncResourceType.WORKFLOW not in selection:
            return []

        # An empty id set means "all workflows" (select-all of the type).
        selected_workflow_ids = selection.get(SyncResourceType.WORKFLOW, set())
        if not selected_workflow_ids:
            return await self._list_projectable_workflows(workflow_ids=None)

        # Build lookup tables once so closure expansion is pure in-memory work.
        all_workflows = await self._list_projectable_workflows(workflow_ids=None)
        workflows_by_id = {workflow.id: workflow for workflow in all_workflows}
        aliases = {
            workflow.alias: workflow.id
            for workflow in all_workflows
            if workflow.alias is not None
        }
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        mgmt_service = WorkflowsManagementService(session=self.session, role=self.role)
        dsl_by_id: dict[uuid.UUID, DSLInput] = {}

        async def dsl_for(workflow: Workflow) -> DSLInput:
            """Return the workflow's DSL, caching it across closure expansion."""
            if workflow.id not in dsl_by_id:
                dsl_by_id[workflow.id] = await self._get_workflow_dsl(
                    workflow,
                    defn_service=defn_service,
                    mgmt_service=mgmt_service,
                )
            return dsl_by_id[workflow.id]

        # Breadth-first walk over child-workflow references, adding each newly
        # discovered child once so cycles terminate.
        included = set(selected_workflow_ids)
        queue = deque(selected_workflow_ids)
        while queue:
            workflow_id = queue.popleft()
            workflow = workflows_by_id.get(workflow_id)
            if workflow is None:
                continue
            dsl = await dsl_for(workflow)
            references = workflow_references(dsl)
            # Children referenced by alias must resolve to a known workflow.
            for alias in sorted(references.execute_aliases):
                child_id = aliases.get(alias)
                if child_id is None:
                    self.logger.warning(
                        "Skipping unresolvable child workflow alias in export closure",
                        alias=alias,
                        parent_workflow_id=str(workflow_id),
                    )
                    continue
                if child_id in included:
                    continue
                included.add(child_id)
                queue.append(child_id)
            # Children referenced directly by id.
            for child_id in sorted(references.execute_ids, key=str):
                if child_id not in workflows_by_id:
                    self.logger.warning(
                        "Skipping unresolvable child workflow reference in export closure",
                        child_workflow_id=str(child_id),
                        parent_workflow_id=str(workflow_id),
                    )
                    continue
                if child_id in included:
                    continue
                included.add(child_id)
                queue.append(child_id)

        # Return the closure in the original global creation order.
        return [workflow for workflow in all_workflows if workflow.id in included]

    async def _get_workflow_dsl(
        self,
        workflow: Workflow,
        *,
        defn_service: WorkflowDefinitionsService,
        mgmt_service: WorkflowsManagementService,
    ) -> DSLInput:
        """Return a workflow's DSL, preferring its committed definition.

        Falls back to building the DSL from the workflow graph when no stored
        definition content exists.
        """
        definition = await defn_service.get_definition_by_workflow_id(
            WorkflowUUID.new(workflow.id)
        )
        if definition and definition.content:
            return DSLInput.model_validate(definition.content)
        return await mgmt_service.build_dsl_from_workflow(workflow)

    async def _local_ids_from_resource_refs(
        self,
        resources: list[ResourceRef] | None,
    ) -> dict[SyncResourceType, set[uuid.UUID]] | None:
        """Resolve resource references into a per-type set of local UUIDs.

        Direct ``local_id`` refs pass through; ``source_id`` refs are resolved
        via their sync mapping and raise :class:`TracecatValidationError` when no
        mapping exists. A ref with neither id selects the whole resource type.
        Returns ``None`` when ``resources`` is empty (a full-workspace export).
        """
        if not resources:
            return None
        resource_ids: dict[SyncResourceType, set[uuid.UUID]] = {}
        for resource in resources:
            if resource.local_id is not None:
                resource_ids.setdefault(resource.resource_type, set()).add(
                    resource.local_id
                )
                continue
            if resource.source_id is None:
                resource_ids.setdefault(resource.resource_type, set())
                continue
            mapping = await self._mapping_by_source_id(
                resource_type=resource.resource_type.value,
                source_id=resource.source_id,
            )
            if mapping is None:
                raise TracecatValidationError(
                    "No sync resource mapping found for "
                    f"{resource.resource_type.value} source id "
                    f"{resource.source_id!r}"
                )
            resource_ids.setdefault(resource.resource_type, set()).add(mapping.local_id)
        return resource_ids

    def _filtered_non_workflow_spec(
        self,
        *,
        full_workspace_export: bool,
        workflow_specs: dict[str, WorkflowResourceSpec],
        resource_ids: dict[SyncResourceType, set[uuid.UUID]] | None,
        projected_resources: list[ProjectedResource],
        projected_spec: WorkspaceSpec,
    ) -> tuple[WorkspaceSpec, list[ProjectedResource]]:
        """Trim non-workflow resources to the dependency closure of a selection.

        A full-workspace export keeps everything. A partial export starts from
        the directly selected resources, then pulls in the non-workflow
        resources the selected workflows and presets depend on: presets and
        skills referenced by slug (expanded transitively through subagents and
        skill bindings), case tags from case triggers, and variables, secrets,
        and tables referenced anywhere in the included payloads. Returns the
        filtered spec alongside the projected resources that survived the filter.
        """
        if full_workspace_export:
            return projected_spec, projected_resources

        # ``desired`` accumulates, per resource type, the source ids that must
        # ship with this partial export. Seed it with the directly selected ids.
        direct_source_ids = _direct_source_ids_by_type(
            resource_ids=resource_ids or {},
            projected_resources=projected_resources,
        )
        desired = {
            resource_type: set(source_ids)
            for resource_type, source_ids in direct_source_ids.items()
        }
        known_table_names = {
            table.name: source_id for source_id, table in projected_spec.tables.items()
        }

        # Add presets the workflows invoke by slug, and case tags their case
        # triggers filter on.
        payloads: list[Any] = list(workflow_specs.values())
        desired.setdefault(SyncResourceType.AGENT_PRESET, set()).update(
            _preset_source_ids_for_slugs(
                projected_spec,
                {
                    slug
                    for workflow in workflow_specs.values()
                    for slug in workflow_references(workflow.definition).preset_slugs
                },
            )
        )
        desired.setdefault(SyncResourceType.CASE_TAG, set()).update(
            source_id
            for workflow in workflow_specs.values()
            if workflow.case_trigger is not None
            for source_id in workflow.case_trigger.tag_filters
            if source_id in projected_spec.case_tags
        )

        # Presets pull in further presets (subagents) and skills, which can in
        # turn reference more presets. Iterate to a fixed point so the closure is
        # complete. ``payloads`` is rebuilt each pass to include the new presets
        # so the variable/secret/table scan below sees their references too.
        while True:
            added = False
            included_presets = [
                projected_spec.agent_presets[source_id]
                for source_id in sorted(
                    desired.get(SyncResourceType.AGENT_PRESET, set())
                )
                if source_id in projected_spec.agent_presets
            ]
            payloads = [*list(workflow_specs.values()), *included_presets]
            for preset in included_presets:
                # Each subagent slug may resolve to another preset to include.
                for subagent in preset.subagents:
                    for source_id in _preset_source_ids_for_slugs(
                        projected_spec,
                        {subagent.slug},
                    ):
                        preset_sources = desired.setdefault(
                            SyncResourceType.AGENT_PRESET,
                            set(),
                        )
                        if source_id not in preset_sources:
                            preset_sources.add(source_id)
                            added = True
                # And each skill binding resolves to a skill to include.
                skill_sources = desired.setdefault(SyncResourceType.SKILL, set())
                for binding in preset.skills:
                    for source_id in _skill_source_ids_for_slugs(
                        projected_spec,
                        {binding.slug},
                    ):
                        if source_id not in skill_sources:
                            skill_sources.add(source_id)
                            added = True
            # No new presets/skills this pass means the closure is complete.
            if not added:
                break

        # Scan the included workflow + preset payloads for VARS./SECRETS./table
        # references and pull in whichever of those resources actually exist.
        desired.setdefault(SyncResourceType.VARIABLE, set()).update(
            source_id
            for name in _variable_names(payloads)
            if (source_id := f"default/{name}") in projected_spec.variables
        )
        desired.setdefault(SyncResourceType.SECRET_METADATA, set()).update(
            source_id
            for name in _secret_names(payloads)
            if (source_id := f"default/{name}") in projected_spec.secret_metadata
        )
        desired.setdefault(SyncResourceType.TABLE, set()).update(
            source_id
            for name, source_id in known_table_names.items()
            if _payloads_reference_table(payloads, name)
        )

        # Materialize the filtered spec and the matching projected resources.
        filtered = workspace_spec_from_maps(
            {
                adapter.spec_attr: _filter_specs(
                    adapter.specs(projected_spec),
                    desired.get(adapter.resource_type, set()),
                )
                for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS
            }
        )
        included_resource_types = {
            adapter.resource_type.value
            for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS
            if adapter.specs(filtered)
        }
        filtered_resources = [
            resource
            for resource in projected_resources
            if resource.source_id in desired.get(resource.resource_type, set())
            and resource.resource_type.value in included_resource_types
        ]
        return filtered, filtered_resources

    async def _source_id_for_workflow(
        self,
        *,
        workflow: Workflow,
        create: bool,
        reserved_source_ids: set[str],
    ) -> str:
        """Return the source id for a workflow, minting one when none is mapped.

        Reuses the existing sync mapping if present. Otherwise derives a unique
        id from the workflow's short UUID, avoiding ``reserved_source_ids`` and
        existing mappings, and persists a new mapping when ``create`` is set.
        """
        mapping = await self._mapping_by_local_id(
            resource_type=SyncResourceType.WORKFLOW.value,
            local_id=WorkflowUUID.new(workflow.id),
        )
        if mapping is not None:
            return mapping.source_id
        preferred_source_id = WorkflowUUID.new(workflow.id).short()
        source_id = await self._unique_source_id(
            resource_type=SyncResourceType.WORKFLOW.value,
            preferred_source_id=preferred_source_id,
            reserved_source_ids=reserved_source_ids,
        )
        if create:
            await self._upsert_mapping(
                resource_type=SyncResourceType.WORKFLOW.value,
                source_id=source_id,
                source_path=workflow_source_path(source_id),
                local_id=WorkflowUUID.new(workflow.id),
            )
        return source_id

    async def _unique_source_id(
        self,
        *,
        resource_type: str,
        preferred_source_id: str,
        reserved_source_ids: set[str],
    ) -> str:
        """Return ``preferred_source_id`` or a ``-N`` suffixed variant that is free.

        Suffixes are appended until the candidate is neither in
        ``reserved_source_ids`` nor already mapped for ``resource_type``.
        """
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
        """Return whether a sync mapping already claims ``source_id``."""
        return (
            await self._mapping_by_source_id(
                resource_type=resource_type,
                source_id=source_id,
            )
            is not None
        )

    async def _resolve_local_workflow_id(self, source_id: str) -> WorkflowUUID:
        """Resolve a workflow ``source_id`` to its local workflow UUID.

        Prefers the sync mapping. Falls back to treating ``source_id`` as a
        legacy workflow id and matching an existing workflow; when neither
        resolves, a fresh UUID is minted for a brand-new import.
        """
        mapping = await self._mapping_by_source_id(
            resource_type=SyncResourceType.WORKFLOW.value,
            source_id=source_id,
        )
        if mapping is not None:
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

    async def _mapping_by_source_id(
        self,
        *,
        resource_type: str,
        source_id: str,
    ) -> WorkspaceSyncResourceMapping | None:
        """Return the sync mapping for ``(resource_type, source_id)``, if any."""
        stmt = select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == resource_type,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _mapping_by_local_id(
        self,
        *,
        resource_type: str,
        local_id: uuid.UUID,
    ) -> WorkspaceSyncResourceMapping | None:
        """Return the sync mapping for ``(resource_type, local_id)``, if any."""
        stmt = select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == resource_type,
            WorkspaceSyncResourceMapping.local_id == local_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _upsert_mapping(
        self,
        *,
        resource_type: str,
        source_id: str,
        source_path: str,
        local_id: uuid.UUID,
    ) -> WorkspaceSyncResourceMapping:
        """Create or update the sync mapping for ``source_id``.

        Refreshes ``source_path`` and ``local_id`` on an existing mapping, or
        inserts a new one, then flushes so the row is visible within the current
        transaction.
        """
        mapping = await self._mapping_by_source_id(
            resource_type=resource_type,
            source_id=source_id,
        )
        if mapping is None:
            mapping = WorkspaceSyncResourceMapping(
                workspace_id=self.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=resource_type,
                source_id=source_id,
                local_id=local_id,
            )
        mapping.source_path = source_path
        mapping.local_id = local_id
        self.session.add(mapping)
        await self.session.flush()
        return mapping

    def _files_from_spec(
        self,
        *,
        manifest: WorkspaceManifest,
        spec: WorkspaceSpec,
    ) -> dict[str, str]:
        """Serialize a manifest and spec into the repository's path-to-content map."""
        return serialize_workspace_spec_files(
            manifest=manifest,
            spec=spec,
            manifest_filename=MANIFEST_FILENAME,
            manifest_serializer=canonical_json_text,
        )

    def _resource_counts_from_spec(
        self,
        spec: WorkspaceSpec,
        *,
        imported_workflows: int = 0,
    ) -> dict[str, ResourcePullCount]:
        """Build per-type found/imported counts from a spec.

        Only workflows report a non-zero ``imported`` value here; other resource
        types are filled in later by :meth:`_resource_counts_from_imported`.
        """
        return {
            resource_type: ResourcePullCount(
                found=found,
                imported=imported_workflows
                if resource_type == SyncResourceType.WORKFLOW.value
                else 0,
            )
            for resource_type, found in spec.resource_count_map().items()
        }

    def _resource_counts_from_imported(
        self,
        spec: WorkspaceSpec,
        imported_resources: list[ImportedResource],
        *,
        imported_workflows: int,
    ) -> dict[str, ResourcePullCount]:
        """Overlay actual import counts for non-workflow resources onto the spec counts."""
        counts = self._resource_counts_from_spec(
            spec,
            imported_workflows=imported_workflows,
        )
        imported_by_type = Counter(
            imported.resource_type.value for imported in imported_resources
        )
        for resource_type, imported_count in imported_by_type.items():
            if resource_type in counts:
                counts[resource_type] = replace(
                    counts[resource_type],
                    imported=imported_count,
                )
        return counts

    def _has_non_workflow_resources(self, spec: WorkspaceSpec) -> bool:
        """Return whether ``spec`` carries any non-workflow resource."""
        return any(adapter.specs(spec) for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS)

    async def _workspace_git_url(self, *, provider: VcsProvider) -> GitUrl:
        """Resolve the workspace's configured Git repository URL.

        Raises :class:`TracecatSettingsError` when no URL is configured or it is
        invalid, and :class:`TracecatValidationError` for providers other than
        GitHub, which is the only one currently supported.
        """
        workspace = await self._workspace()
        repo_url = (
            workspace.settings.get("git_repo_url") if workspace.settings else None
        )
        if not repo_url:
            raise TracecatSettingsError(
                "Git repository URL not configured for this workspace."
            )
        if provider != VcsProvider.GITHUB:
            raise TracecatValidationError(
                f"{provider.value} workspace sync is not implemented yet."
            )
        try:
            return parse_git_url(repo_url, allowed_domains={"github.com"})
        except ValueError as e:
            raise TracecatSettingsError(
                f"Invalid Git repository URL configured for this workspace: {e}"
            ) from e

    async def _workspace(self) -> Workspace:
        """Load the current workspace, raising :class:`TracecatNotFoundError` if absent."""
        workspace = await WorkspaceService(
            session=self.session,
            role=self.role,
        ).get_workspace(self.workspace_id)
        if workspace is None:
            raise TracecatNotFoundError("Workspace not found")
        return workspace


_VAR_REF_RE = re.compile(r"\bVARS\.([A-Za-z_][A-Za-z0-9_-]*)")
"""Capture the ``<name>`` in a ``VARS.<name>`` expression reference."""
_SECRET_REF_RE = re.compile(r"\bSECRETS\.([A-Za-z_][A-Za-z0-9_-]*)")
"""Capture the ``<name>`` in a ``SECRETS.<name>`` expression reference."""


def _selection_from_workflow_ids(
    *,
    workflow_ids: Sequence[WorkflowUUID] | None,
    resource_ids: dict[SyncResourceType, set[uuid.UUID]] | None,
) -> dict[SyncResourceType, set[uuid.UUID]] | None:
    """Merge explicit ``workflow_ids`` into a per-type resource selection.

    Returns ``None`` (the full-workspace sentinel) only when both inputs are
    ``None``. Provided ``workflow_ids`` always set the
    :attr:`SyncResourceType.WORKFLOW` entry, overriding any in ``resource_ids``.
    """
    if workflow_ids is None:
        if resource_ids is None:
            return None
        return {
            resource_type: set(local_ids)
            for resource_type, local_ids in resource_ids.items()
        }

    selection = (
        {
            resource_type: set(local_ids)
            for resource_type, local_ids in resource_ids.items()
        }
        if resource_ids is not None
        else {}
    )
    selection[SyncResourceType.WORKFLOW] = {
        uuid.UUID(str(workflow_id)) for workflow_id in workflow_ids
    }
    return selection


def _non_workflow_resource_types_for_projection(
    selection: dict[SyncResourceType, set[uuid.UUID]] | None,
) -> set[SyncResourceType] | None:
    """Return the non-workflow resource types that can be projected directly.

    ``None`` means the projector must load every non-workflow type. Full exports
    and workflow-containing selections need the full metadata surface because
    workflow and preset dependency closure can discover any supported resource
    type transitively.
    """
    if selection is None or SyncResourceType.WORKFLOW in selection:
        return None
    return {
        resource_type
        for resource_type in selection
        if resource_type != SyncResourceType.WORKFLOW
    }


def _direct_source_ids_by_type(
    *,
    resource_ids: dict[SyncResourceType, set[uuid.UUID]],
    projected_resources: list[ProjectedResource],
) -> dict[SyncResourceType, set[str]]:
    """Map the selected local ids to the source ids directly chosen for export.

    A resource type whose selected id set is empty is treated as "select all"
    of that type, so every projected resource of that type is included.
    """
    direct: dict[SyncResourceType, set[str]] = {}
    for resource in projected_resources:
        selected_local_ids = resource_ids.get(resource.resource_type)
        if selected_local_ids is None:
            continue
        if not selected_local_ids or resource.local_id in selected_local_ids:
            direct.setdefault(resource.resource_type, set()).add(resource.source_id)
    return direct


def _preset_source_ids_for_slugs(
    spec: WorkspaceSpec,
    slugs: set[str],
) -> set[str]:
    """Return the source ids of agent presets whose slug is in ``slugs``."""
    return {
        source_id
        for source_id, preset in spec.agent_presets.items()
        if preset.slug in slugs
    }


def _skill_source_ids_for_slugs(
    spec: WorkspaceSpec,
    slugs: set[str],
) -> set[str]:
    """Return the source ids of skills whose slug is in ``slugs``."""
    return {
        source_id for source_id, skill in spec.skills.items() if skill.slug in slugs
    }


def _export_read_scopes_for_spec(spec: WorkspaceSpec) -> set[str]:
    """Collect the read scopes implied by the resource types present in ``spec``."""
    scopes: set[str] = set()
    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        if adapter.specs(spec) and (
            scope := _EXPORT_READ_SCOPES_BY_RESOURCE_TYPE.get(adapter.resource_type)
        ):
            scopes.add(scope)
    return scopes


def _filter_specs[T](specs: dict[str, T], source_ids: set[str] | None) -> dict[str, T]:
    """Restrict ``specs`` to ``source_ids``, ordered by id; empty selection drops all."""
    if not source_ids:
        return {}
    return {
        source_id: specs[source_id]
        for source_id in sorted(source_ids)
        if source_id in specs
    }


def _variable_names(payloads: list[Any]) -> set[str]:
    """Return the ``VARS.<name>`` variable names referenced anywhere in ``payloads``."""
    return {
        match
        for text in _payload_strings(payloads)
        for match in _VAR_REF_RE.findall(text)
    }


def _secret_names(payloads: list[Any]) -> set[str]:
    """Return the ``SECRETS.<name>`` secret names referenced anywhere in ``payloads``."""
    return {
        match
        for text in _payload_strings(payloads)
        for match in _SECRET_REF_RE.findall(text)
    }


def _payloads_reference_table(payloads: list[Any], table_name: str) -> bool:
    """Return whether any payload references ``table_name``.

    Matches an exact ``table``/``table_name``/``table_slug`` field value as well
    as a whole-word occurrence of the name inside any string value.
    """
    table_pattern = re.compile(
        rf"(?<![A-Za-z0-9_.-]){re.escape(table_name)}(?![A-Za-z0-9_.-])"
    )
    for key, value in _payload_key_values(payloads):
        if (
            key in {"table", "table_name", "table_slug"}
            and isinstance(value, str)
            and value == table_name
        ):
            return True
        if isinstance(value, str) and table_pattern.search(value):
            return True
    return False


def _payload_strings(payloads: list[Any]) -> list[str]:
    """Return every string value found while walking ``payloads``."""
    return [
        value for _key, value in _payload_key_values(payloads) if isinstance(value, str)
    ]


def _payload_key_values(payloads: list[Any]) -> list[tuple[str | None, Any]]:
    """Flatten ``payloads`` into ``(key, value)`` pairs over their JSON form."""
    values: list[tuple[str | None, Any]] = []
    for payload in payloads:
        values.extend(_walk_payload(_json_payload(payload), key=None))
    return values


def _walk_payload(value: Any, *, key: str | None) -> list[tuple[str | None, Any]]:
    """Recursively collect ``(key, value)`` pairs from a nested JSON structure.

    Dict children carry their own key; list items inherit the parent's key.
    """
    values = [(key, value)]
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            values.extend(_walk_payload(child_value, key=str(child_key)))
    elif isinstance(value, list):
        for child_value in value:
            values.extend(_walk_payload(child_value, key=key))
    return values


def _json_payload(payload: Any) -> Any:
    """Return a JSON-compatible view of ``payload``, dumping Pydantic models."""
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json", exclude_none=True)
    return payload


def _spec_for_pull_options(
    spec: WorkspaceSpec,
    *,
    sync_schedules: bool,
) -> WorkspaceSpec:
    """Project the pull target spec, stripping workflow schedules unless opted in.

    When ``sync_schedules`` is ``False`` the spec is copied with every
    workflow's ``schedules`` cleared so a diff or import never touches schedule
    configuration.
    """
    if sync_schedules:
        return spec
    return spec.model_copy(
        update={
            "workflows": {
                source_id: workflow.model_copy(update={"schedules": None})
                for source_id, workflow in spec.workflows.items()
            }
        }
    )


def _resource_identity_from_path(
    path: str,
    *,
    roots: WorkspaceManifestResources,
) -> tuple[ResourceAdapter, str] | None:
    """Resolve a repository path to its ``(adapter, source_id)``, or ``None``.

    Tries each adapter's primary-file mapping first, then its companion-file
    mapping, so both kinds of path resolve to the owning resource.
    """
    for adapter in (WORKFLOW_RESOURCE_ADAPTER, *NON_WORKFLOW_RESOURCE_ADAPTERS):
        if source_id := adapter.source_id_from_path(path, roots):
            return adapter, source_id
        if extra := adapter.extra_path_from_path(path, roots):
            source_id, _relative_path = extra
            return adapter, source_id
    return None


def _unified_resource_diff(
    *,
    path: str,
    before: str | None,
    after: str,
) -> tuple[str, bool]:
    """Build a unified diff of ``before`` versus ``after`` for ``path``.

    Returns the diff text and whether it was truncated to
    :data:`MAX_PULL_RESOURCE_DIFF_LINES`.
    """
    lines = list(
        unified_diff(
            (before or "").splitlines(),
            after.splitlines(),
            fromfile=f"current/{path}",
            tofile=f"incoming/{path}",
            lineterm="",
        )
    )
    truncated = len(lines) > MAX_PULL_RESOURCE_DIFF_LINES
    if truncated:
        lines = [
            *lines[:MAX_PULL_RESOURCE_DIFF_LINES],
            "... diff truncated ...",
        ]
    return "\n".join(lines), truncated


def _resource_title(
    spec: WorkspaceSpec,
    *,
    adapter: ResourceAdapter,
    source_id: str,
) -> str | None:
    """Return a human-readable title for a resource, falling back to ``source_id``."""
    resource = adapter.specs(spec).get(source_id)
    if resource is None:
        return source_id
    return adapter.display_name(resource) or source_id
