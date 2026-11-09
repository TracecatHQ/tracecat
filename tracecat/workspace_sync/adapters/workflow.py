"""Workflow adapter registry shim.

Unlike the non-workflow adapters, this class does not project ORM rows or import
specs by itself. Workflows need DSL resolution, dependency closure handling, and
``WorkflowImportService`` integration, so ``WorkspaceSyncService`` owns that
runtime behavior.

This adapter keeps workflows visible to the generic workspace-sync registry for
path lookup, manifest/spec assembly, dependency diagnostics, preview labels,
and the Python script files that sit next to each ``definition.yml``.
Workflow-specific conversion and YAML parsing live in
``tracecat.workspace_sync.workflow``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel

from tracecat.sync import PullDiagnostic
from tracecat.workspace_sync.adapters.base import (
    DirectoryManifestAdapter,
    path_parts,
    path_segment,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    WORKFLOW_DEFINITION_FILENAME,
    WORKFLOW_ROOT,
    WorkflowResourceSpec,
    WorkspaceManifestResources,
)
from tracecat.workspace_sync.types import PathsByType
from tracecat.workspace_sync.workflow import (
    WORKFLOW_SCRIPTS_DIR,
    serialize_workflow_spec,
    workflow_script_files,
    workflow_spec_with_script_files,
)


class WorkflowAdapter(DirectoryManifestAdapter):
    """Registry adapter for workflow paths; projection/import live in the service."""

    resource_type = SyncResourceType.WORKFLOW
    spec_attr = "workflows"
    model = WorkflowResourceSpec
    read_scope = "workflow:read"
    create_scope = "workflow:create"
    update_scope = "workflow:update"
    root = WORKFLOW_ROOT
    filename = WORKFLOW_DEFINITION_FILENAME

    def path_key(self, source_id: str, spec: BaseModel) -> str:
        """Name the workflow folder after its title, as shown in the app."""
        title = cast(WorkflowResourceSpec, spec).definition.title
        return path_segment(title, fallback=path_segment(source_id))

    def display_name(self, spec: BaseModel) -> str | None:
        """Use the workflow definition title, falling back to the base label."""
        if isinstance(spec, WorkflowResourceSpec):
            return spec.definition.title
        return super().display_name(spec)

    def companion_relpath(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Return a script file's path inside its workflow folder.

        ``workflows/Triage/scripts/enrich_ip.py`` -> ``scripts/enrich_ip.py``.
        """
        parts = path_parts(path)
        root_parts = path_parts(roots.workflows)
        # Must be exactly <root>/<folder>/scripts/<file>.py.
        if len(parts) != len(root_parts) + 3:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        if parts[-2] != WORKFLOW_SCRIPTS_DIR or not parts[-1].endswith(".py"):
            return None
        return "/".join(parts[-2:])

    def serialize_primary(self, spec: BaseModel) -> str:
        """Write ``definition.yml``, without the scripts ``scripts/`` holds."""
        return serialize_workflow_spec(cast(WorkflowResourceSpec, spec))

    def serialize_extra_files(
        self,
        primary_path: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Write each Python script to ``scripts/<action ref>.py``."""
        folder = self.location(primary_path)
        scripts = workflow_script_files(cast(WorkflowResourceSpec, spec))
        return {f"{folder}/{relpath}": content for relpath, content in scripts.items()}

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
        *,
        paths: PathsByType,
    ) -> dict[str, BaseModel]:
        """Put each script file back into the action it belongs to.

        A script file that matches no ``core.script.run_python`` action gets a
        diagnostic, so the pull fails. That is what renaming an action's ref
        without its file leaves behind, and importing would drop the script.
        """
        scripts_by_source: dict[str, dict[str, str]] = defaultdict(dict)
        for (source_id, relpath), content in extra_files.items():
            scripts_by_source[source_id][relpath] = content
        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec, unused = workflow_spec_with_script_files(
                cast(WorkflowResourceSpec, base_spec),
                scripts_by_source.get(source_id, {}),
            )
            folder = self.location(self.path_in(paths, source_id))
            for relpath in unused:
                path = f"{folder}/{relpath}"
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=path,
                        workflow_title=spec.definition.title,
                        error_type="validation",
                        message=(
                            f"{path} matches no Python script action in this "
                            "workflow. If you renamed an action's ref, rename its "
                            "script file to match. Otherwise, delete the file."
                        ),
                        details={"path": path},
                    )
                )
            updated[source_id] = spec
        return updated
