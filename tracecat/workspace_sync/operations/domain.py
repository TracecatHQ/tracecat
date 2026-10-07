"""Prepare immutable Git sync inputs using the existing adapters and transports."""

import hashlib
import uuid
from dataclasses import replace

import orjson
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from temporalio.exceptions import ApplicationError

from tracecat.db.locks import try_pg_advisory_xact_lock
from tracecat.db.models import Action, Workflow, WorkspaceSyncResourceMapping
from tracecat.exceptions import TracecatValidationError
from tracecat.sync import PullResult, PushStatus
from tracecat.tiers.enums import Entitlement
from tracecat.workspace_sync.operations.schemas import (
    PreparedSync,
    SyncOperationCreate,
    SyncPushResult,
)
from tracecat.workspace_sync.operations.types import (
    FetchedSync,
    StaleSyncPreviewError,
    SyncPreviewSummary,
    lock_key,
)
from tracecat.workspace_sync.schemas import (
    WorkspaceProjection,
    WorkspaceSyncExportPreview,
)
from tracecat.workspace_sync.service import (
    WorkspaceSyncService,
    _export_read_scopes_for_spec,
    _preview_resources_from_spec,
)
from tracecat.workspace_sync.transport import (
    BaseWorkspaceSyncTransport,
    sync_commit_message,
)


def fingerprint(value: object) -> str:
    """Hash a canonical JSON snapshot for optimistic concurrency checks."""
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()


class DurableSyncService(WorkspaceSyncService):
    """Adapt the sync domain service to a durable prepare/apply lifecycle."""

    async def repository_fingerprint(self) -> str:
        workspace = await self._workspace()
        return fingerprint(
            {"provider": self._mapping_provider.value, "settings": workspace.settings}
        )

    async def local_fingerprint(
        self,
        inputs: SyncOperationCreate,
        *,
        projection: WorkspaceProjection | None = None,
    ) -> str:
        if projection is None:
            resource_ids = (
                await self._local_ids_from_resource_refs(inputs.push.resources)
                if inputs.push
                else None
            )
            projection = await self.project_workspace(
                resource_ids=resource_ids,
                include_schedules=inputs.push.include_schedules
                if inputs.push
                else bool(inputs.pull and inputs.pull.sync_schedules),
                create_missing_mappings=False,
            )
        if inputs.push:
            return fingerprint(projection.files)
        # Exported workflows prefer published definitions. Pull also replaces the
        # editable graph, including drafts that cannot yet be compiled as DSL.
        workflows: list[JsonValue] = list(
            await self.session.scalars(
                select(
                    func.jsonb_build_object(
                        "id",
                        Workflow.id,
                        "title",
                        Workflow.title,
                        "description",
                        Workflow.description,
                        "expects",
                        Workflow.expects,
                        "returns",
                        Workflow.returns,
                        "config",
                        Workflow.config,
                        "error_handler",
                        Workflow.error_handler,
                        "alias",
                        Workflow.alias,
                        "version",
                        Workflow.version,
                        "folder_id",
                        Workflow.folder_id,
                    )
                )
                .where(Workflow.workspace_id == self.workspace_id)
                .order_by(Workflow.id)
            )
        )
        actions: list[JsonValue] = list(
            await self.session.scalars(
                select(
                    func.jsonb_build_object(
                        "id",
                        Action.id,
                        "workflow_id",
                        Action.workflow_id,
                        "type",
                        Action.type,
                        "title",
                        Action.title,
                        "description",
                        Action.description,
                        "status",
                        Action.status,
                        "inputs",
                        Action.inputs,
                        "control_flow",
                        Action.control_flow,
                        "is_interactive",
                        Action.is_interactive,
                        "interaction",
                        Action.interaction,
                        "environment",
                        Action.environment,
                        "upstream_edges",
                        Action.upstream_edges,
                        "position_x",
                        Action.position_x,
                        "position_y",
                        Action.position_y,
                    )
                )
                .where(Action.workspace_id == self.workspace_id)
                .order_by(Action.id)
            )
        )
        # Viewport, trigger position, graph version, and audit timestamps are
        # editor metadata preserved by import, not part of the reviewed draft.
        return fingerprint(
            {"files": projection.files, "workflows": workflows, "actions": actions}
        )

    async def fetch_remote(
        self, inputs: SyncOperationCreate, *, release_read_session: bool = False
    ) -> FetchedSync:
        """Fetch immutable Git contents before taking a local database snapshot."""
        await self.require_entitlement(Entitlement.GIT_SYNC)
        self._require_sync_operation_scope()
        repository_fingerprint = await self.repository_fingerprint()
        url = await self._workspace_git_url()
        transport = self._transport_for_provider()
        if isinstance(transport, BaseWorkspaceSyncTransport):
            transport.release_read_session = release_read_session
        target_exists = True
        if params := inputs.push:
            self._validate_export_params(params)
            target_exists = await transport.branch_exists(url=url, branch=params.branch)
            compare_ref = (
                params.branch
                if target_exists
                else params.pr_base_branch or inputs.compare_ref or url.ref
            )
            if not compare_ref:
                branches = await transport.list_branches(url=url, limit=10000)
                compare_ref = next(
                    (branch.name for branch in branches if branch.is_default), None
                )
            if not compare_ref:
                raise TracecatValidationError("Select a base branch before previewing")
            remote = await transport.read_files(url=url, ref=compare_ref)
        else:
            if inputs.pull is None:
                raise TracecatValidationError("Missing pull inputs")
            remote = await transport.read_files(url=url, ref=inputs.pull.commit_sha)
            compare_ref = inputs.compare_ref or inputs.pull.commit_sha
        return FetchedSync(remote, repository_fingerprint, compare_ref, target_exists)

    async def prepare(
        self, inputs: SyncOperationCreate, *, fetched: FetchedSync | None = None
    ) -> PreparedSync:
        """Validate remote bytes against one coherent local database snapshot."""
        fetched = fetched or await self.fetch_remote(inputs)
        await self.require_entitlement(Entitlement.GIT_SYNC)
        self._require_sync_operation_scope()
        repository_fingerprint = await self.repository_fingerprint()
        if repository_fingerprint != fetched.repository_fingerprint:
            raise StaleSyncPreviewError("Repository settings changed during fetch")
        remote = fetched.remote
        if params := inputs.push:
            self._validate_export_params(params)
            resource_ids = await self._local_ids_from_resource_refs(params.resources)
            projection = await self.project_export_preview(
                resource_ids=resource_ids,
                include_schedules=params.include_schedules,
            )
            roots = list(
                await self._export_delete_roots(
                    projection,
                    full_workspace_export=resource_ids is None,
                    resource_ids=resource_ids,
                )
            )
            preview = WorkspaceSyncExportPreview(
                resource_counts=projection.spec.resource_count_map(),
                files=sorted(projection.files),
                resources=_preview_resources_from_spec(projection.spec),
                resource_diffs=self._resource_diffs_for_export(
                    projection, remote, delete_missing_paths_under=roots
                ),
            )
            return PreparedSync(
                projection=projection,
                preview=preview,
                workspace_fingerprint=fingerprint(projection.files),
                repository_fingerprint=repository_fingerprint,
                compare_ref=fetched.compare_ref,
                compare_sha=remote.commit_sha,
                target_exists=fetched.target_exists,
                delete_roots=roots,
            )
        params = inputs.pull
        if params is None:
            raise TracecatValidationError("Missing pull inputs")
        snapshot, diagnostics = await self.parse_files(
            remote.files, commit_sha=remote.commit_sha, tree_sha=remote.tree_sha
        )
        prepared = await self.prepare_pull_preview(
            snapshot,
            sync_schedules=params.sync_schedules,
            parse_diagnostics=diagnostics,
            requested_catalog_mappings={
                mapping.source_catalog_id: mapping.target_catalog_id
                for mapping in params.catalog_mappings
            },
            requested_mcp_integration_mappings={
                mapping.source_mcp_integration_id: mapping.target_mcp_integration_id
                for mapping in params.mcp_integration_mappings
            },
        )
        return PreparedSync(
            snapshot=prepared.snapshot,
            preview=prepared.preview,
            workspace_fingerprint=await self.local_fingerprint(inputs),
            repository_fingerprint=repository_fingerprint,
            compare_ref=fetched.compare_ref,
            compare_sha=remote.commit_sha,
        )

    async def apply(
        self,
        inputs: SyncOperationCreate,
        prepared: PreparedSync,
        operation_id: uuid.UUID,
    ) -> SyncPushResult | PullResult:
        """Apply exactly the reviewed state within the caller's transaction."""
        await self.require_entitlement(Entitlement.GIT_SYNC)
        if await self.repository_fingerprint() != prepared.repository_fingerprint:
            raise StaleSyncPreviewError("Repository settings changed; preview again")
        url = await self._workspace_git_url()
        transport = self._transport_for_provider()
        # Never wait on a repository lock inside an established snapshot.
        # A contended repository gets a fresh transaction on the activity retry.
        if not await try_pg_advisory_xact_lock(
            self.session,
            lock_key(
                f"git-sync/{url.host}/{url.org}/{url.repo}/{inputs.push.branch if inputs.push else prepared.compare_ref}"
            ),
        ):
            raise RuntimeError("Repository sync is already in progress")
        if params := inputs.push:
            projection = prepared.projection
            if projection is None:
                raise StaleSyncPreviewError("Missing prepared projection")
            self._require_workspace_sync_scope()
            self._require_projected_export_scopes(projection.spec)
            await self._require_spec_entitlements(projection.spec)
            target_exists = await transport.branch_exists(url=url, branch=params.branch)
            if prepared.target_exists and not target_exists:
                raise StaleSyncPreviewError(
                    "Target branch was deleted; create a new preview"
                )
            remote = await transport.read_files(
                url=url,
                ref=params.branch if target_exists else prepared.compare_ref,
            )
            remote_paths = remote.blob_paths or frozenset(remote.files)
            changed_files = any(
                remote.files.get(path) != content
                for path, content in projection.files.items()
            )
            stale_files = any(
                path not in projection.files
                and any(
                    path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/")
                    for root in prepared.delete_roots
                )
                for path in remote_paths
            )
            # A matching tree alone is insufficient: an unrelated writer
            # may have changed other files. Recover only this operation's
            # marked commit, with the exact expected managed contents.
            recovered_sha: str | None = None
            if remote.commit_sha != prepared.compare_sha or (
                target_exists and not prepared.target_exists
            ):
                if changed_files or stale_files or not target_exists:
                    raise StaleSyncPreviewError("Target branch changed; preview again")
                commits = await transport.list_commits(
                    url=url, branch=params.branch, limit=100
                )
                expected_message = sync_commit_message(
                    params.message.strip(), operation_id
                )
                if not commits or commits[0].sha != remote.commit_sha:
                    raise StaleSyncPreviewError("Target branch changed; preview again")
                # A later unrelated commit may follow a write whose response
                # was lost. Bound the search and retain the operation's own SHA.
                recovered_sha = next(
                    (
                        commit.sha
                        for commit in commits
                        if commit.message.rstrip() == expected_message
                    ),
                    None,
                )
                if recovered_sha is None:
                    raise StaleSyncPreviewError("Target branch changed; preview again")
            else:
                # The source guard applies only before a new Git write. A
                # verified operation-marked commit already contains the reviewed
                # bytes; recovery only needs to finish its PR and receipt.
                if (
                    await self.local_fingerprint(inputs)
                    != prepared.workspace_fingerprint
                ):
                    raise StaleSyncPreviewError("Workspace changed; preview again")
            # Retain the identities captured by the reviewed projection, even
            # if recovery happens after a rename. Never overwrite newer mappings.
            for offset in range(0, len(projection.mapping_targets), 500):
                await self.session.execute(
                    insert(WorkspaceSyncResourceMapping)
                    .values(
                        [
                            {
                                "workspace_id": self.workspace_id,
                                "provider": self._mapping_provider_value,
                                "resource_type": target.resource_type,
                                "source_id": target.source_id,
                                "source_path": target.source_path,
                                "local_id": target.local_id,
                            }
                            for target in projection.mapping_targets[
                                offset : offset + 500
                            ]
                        ]
                    )
                    .on_conflict_do_nothing()
                )
            commit = await transport.write_files(
                url=url,
                files=projection.files,
                message=params.message,
                branch=params.branch,
                create_pr=params.create_pr,
                pr_base_branch=params.pr_base_branch,
                branch_start_ref=prepared.compare_ref,
                expected_commit_sha=remote.commit_sha,
                operation_id=operation_id,
                delete_missing_paths_under=prepared.delete_roots,
            )
            if recovered_sha is not None:
                # The transport may report a no-op while recovering the PR.
                # This operation already committed the reviewed contents.
                commit = replace(
                    commit,
                    status=PushStatus.COMMITTED,
                    sha=recovered_sha,
                    message=params.message,
                )
            result = SyncPushResult(commit=commit)
            return result
        else:
            snapshot = prepared.snapshot
            params = inputs.pull
            if snapshot is None or params is None:
                raise StaleSyncPreviewError("Missing prepared snapshot")
            projection = await self.project_workspace(
                include_schedules=params.sync_schedules,
                create_missing_mappings=False,
            )
            if (
                await self.local_fingerprint(inputs, projection=projection)
                != prepared.workspace_fingerprint
            ):
                raise StaleSyncPreviewError("Workspace changed; preview again")
            # Immutable commit selections need no second download. For
            # a branch selection, resolve its head without loading blobs.
            if prepared.compare_ref != prepared.compare_sha:
                commits = await transport.list_commits(
                    url=url, branch=prepared.compare_ref, limit=1
                )
                if not commits or commits[0].sha != prepared.compare_sha:
                    raise StaleSyncPreviewError("Source branch changed; preview again")
            self._require_sync_operation_scope()
            self._require_pull_scopes(snapshot.spec, dry_run=False)
            await self._require_spec_entitlements(snapshot.spec)
            result = await self.import_prepared_snapshot(
                snapshot,
                sync_schedules=params.sync_schedules,
                mapping_targets=projection.mapping_targets,
                changed_resources={
                    (diff.resource_type, diff.source_id)
                    for diff in prepared.preview.resource_diffs or []
                }
                | {("workflow", source_id) for source_id in snapshot.spec.workflows},
            )
            if not result.success:
                raise ApplicationError(
                    "Database import failed and was rolled back",
                    non_retryable=True,
                )
            return result


def preview_summary(prepared: PreparedSync) -> SyncPreviewSummary:
    """Keep polling responses small; pages and file diffs have separate endpoints."""
    preview = prepared.preview
    diff_count = len(preview.resource_diffs or [])
    if isinstance(preview, WorkspaceSyncExportPreview):
        data = preview.model_copy(
            update={"files": [], "resources": [], "resource_diffs": []}
        ).model_dump(mode="json")
    else:
        data = TypeAdapter(PullResult).dump_python(
            replace(preview, files=None, resources=None, resource_diffs=None),
            mode="json",
        )
    spec = (
        prepared.projection.spec
        if prepared.projection
        else prepared.snapshot.spec
        if prepared.snapshot
        else None
    )
    return {
        "preview": data,
        "diff_count": diff_count,
        "read_scopes": sorted(_export_read_scopes_for_spec(spec)) if spec else [],
    }
