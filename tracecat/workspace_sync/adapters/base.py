"""Base class and shared helpers for workspace sync resource adapters.

For most Git-backed workspace resource types, a :class:`ResourceAdapter` owns
the full sync behavior in one place: repository paths, parsing/serialization,
database projection, and database import. Workflows are special-cased: their
adapter contributes registry/path metadata only, while ``WorkspaceSyncService``
handles workflow projection/import directly because workflows need DSL
resolution, dependency closure handling, and workflow-store services.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import WorkspaceSyncResourceMapping
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic
from tracecat.tables.enums import SqlType
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import WorkspaceManifestResources, WorkspaceSpec

# Attribute on ``WorkspaceSpec`` and ``WorkspaceManifestResources`` that holds a
# given resource type's specs/root. Each adapter binds to exactly one of these
# via its ``spec_attr`` class variable.
type WorkspaceSpecField = Literal[
    "workflows",
    "agent_presets",
    "skills",
    "tables",
    "case_tags",
    "case_fields",
    "case_dropdowns",
    "case_durations",
    "variables",
    "secret_metadata",
]


@dataclass(frozen=True, slots=True)
class ProjectedResource:
    """One resource projected out of the database during an export."""

    resource_type: SyncResourceType
    """The kind of resource being synced (workflow, table, ...)."""
    source_id: str
    """Portable, Git-owned identity, e.g. ``my-table`` (or the compound
    ``prod/api-key`` for environment-scoped resources)."""
    source_path: str
    """Where the resource's primary file lives in the repository."""
    local_id: uuid.UUID
    """Workspace-local database UUID this ``source_id`` maps to."""


@dataclass(frozen=True, slots=True)
class ImportedResource:
    """One resource reconciled into the database during an import.

    Carries the same identity as :class:`ProjectedResource`, recorded after the
    spec has been written back to local state so the sync mapping can be
    persisted.
    """

    resource_type: SyncResourceType
    """The kind of resource being synced (workflow, table, ...)."""
    source_id: str
    """Portable, Git-owned identity that maps to ``local_id``."""
    source_path: str
    """Where the resource's primary file lives in the repository."""
    local_id: uuid.UUID
    """Workspace-local database UUID this ``source_id`` maps to."""


@dataclass(frozen=True, slots=True)
class ResourceProjection:
    """The result of projecting one resource type out of the database."""

    specs: dict[str, BaseModel]
    """Map of ``source_id`` to its Git-owned spec model."""
    resources: list[ProjectedResource]
    """Identities linking each ``source_id`` to its ``local_id``, parallel to
    ``specs``."""


@dataclass(frozen=True, slots=True)
class ResourceDependencyRefs:
    """Identifiers used to lazily project resources reached by dependency closure."""

    select_all: bool = False
    """Whether every resource of the target type is selected."""
    local_ids: set[uuid.UUID] = field(default_factory=set)
    """Workspace-local ids selected directly by the export request."""
    source_ids: set[str] = field(default_factory=set)
    """Git-owned source ids referenced directly by another resource."""
    slugs: set[str] = field(default_factory=set)
    """Slug references such as agent preset and skill slugs."""
    names: set[str] = field(default_factory=set)
    """Name references such as table names or environment-agnostic variable names."""
    environment_names: set[tuple[str, str]] = field(default_factory=set)
    """Environment-qualified ``(environment, name)`` references."""


class ResourceAdapter(ABC):
    """One Git-backed workspace resource type.

    Subclasses declare the class-level metadata (``resource_type``,
    ``spec_attr``, ``model``) and override the behavior they support. Resources
    that are not projectable or importable (e.g. workflows, which are handled
    directly by the sync service) simply inherit the no-op defaults.
    """

    resource_type: ClassVar[SyncResourceType]
    """The sync resource type this adapter handles."""
    spec_attr: ClassVar[WorkspaceSpecField]
    """Attribute on ``WorkspaceSpec``/``WorkspaceManifestResources`` for this type."""
    model: ClassVar[type[BaseModel]]
    """Pydantic spec model the resource serializes to and from."""

    # -- paths and serialization ------------------------------------------
    @abstractmethod
    def source_path(self, source_id: str) -> str:
        """Repository path for the resource's primary file."""

    @abstractmethod
    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Return the source id a primary file path maps to, or ``None``.

        Inverse of :meth:`source_path`. ``None`` means the path is not a primary
        file for this resource type.
        """

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map a companion file path to ``(source_id, relative_path)``.

        Companion files are everything beyond the primary file, such as skill
        blobs. Returns ``None`` when ``path`` is not such a file; the default
        has no companion files.
        """
        return None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize companion files such as skill files."""
        return {}

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
    ) -> dict[str, BaseModel]:
        """Fold parsed companion files back into their resource specs."""
        return specs

    # -- database projection and import -----------------------------------
    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project this resource type's database rows into Git specs.

        Returns an empty projection by default; projectable adapters override
        it. Workflows are projected by the sync service, not here.
        """
        return ResourceProjection(specs={}, resources=[])

    async def project_dependency_refs(
        self,
        workspace_service: BaseWorkspaceService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project resources addressed by dependency-closure refs.

        The default implementation filters a full type projection by local id or
        source id. Adapters with natural lookup keys such as slug or
        ``(environment, name)`` override this to avoid full type scans.
        """
        projection = await self.project(workspace_service)
        if refs.select_all:
            return projection

        source_ids = set(refs.source_ids)
        if refs.local_ids:
            source_ids.update(
                resource.source_id
                for resource in projection.resources
                if resource.local_id in refs.local_ids
            )
        if not source_ids:
            return ResourceProjection(specs={}, resources=[])

        return ResourceProjection(
            specs={
                source_id: spec
                for source_id, spec in projection.specs.items()
                if source_id in source_ids
            },
            resources=[
                resource
                for resource in projection.resources
                if resource.source_id in source_ids
            ],
        )

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile this resource type's Git spec slice into the local database.

        Returns nothing by default; importable adapters override it.
        """
        return []

    # -- helpers ----------------------------------------------------------
    def specs(self, spec: WorkspaceSpec) -> dict[str, BaseModel]:
        """Pull this resource type's ``source_id`` -> spec map off a workspace spec."""
        return cast(dict[str, BaseModel], getattr(spec, self.spec_attr))

    def display_name(self, spec: BaseModel) -> str | None:
        """Human-readable resource label for sync preview surfaces."""
        for attr in ("name", "slug", "alias", "id"):
            value = getattr(spec, attr, None)
            if isinstance(value, str) and (cleaned := value.strip()):
                return cleaned
        return None

    def projected_resource(
        self,
        source_id: str,
        local_id: uuid.UUID,
    ) -> ProjectedResource:
        """Build a :class:`ProjectedResource` with this adapter's type and path."""
        return ProjectedResource(
            resource_type=self.resource_type,
            source_id=source_id,
            source_path=self.source_path(source_id),
            local_id=local_id,
        )

    def imported_resource(
        self,
        source_id: str,
        local_id: uuid.UUID,
    ) -> ImportedResource:
        """Build an :class:`ImportedResource` with this adapter's type and path."""
        return ImportedResource(
            resource_type=self.resource_type,
            source_id=source_id,
            source_path=self.source_path(source_id),
            local_id=local_id,
        )

    async def source_ids_by_local_id(
        self,
        workspace_service: BaseWorkspaceService,
    ) -> dict[uuid.UUID, str]:
        """Load this resource type's ``local_id`` -> ``source_id`` sync mappings.

        Used during projection to reuse the source id already assigned to a
        local resource instead of minting a fresh one.
        """
        stmt = select(
            WorkspaceSyncResourceMapping.local_id,
            WorkspaceSyncResourceMapping.source_id,
        ).where(
            WorkspaceSyncResourceMapping.workspace_id == workspace_service.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
        )
        return dict((await workspace_service.session.execute(stmt)).tuples().all())

    async def local_id_for_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        source_id: str,
    ) -> uuid.UUID | None:
        """Resolve a single ``source_id`` to its mapped ``local_id``, if any."""
        stmt = select(WorkspaceSyncResourceMapping.local_id).where(
            WorkspaceSyncResourceMapping.workspace_id == workspace_service.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        return await workspace_service.session.scalar(stmt)

    async def local_ids_by_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        source_ids: Iterable[str],
    ) -> dict[str, uuid.UUID]:
        """Bulk-resolve ``source_id`` values to their mapped ``local_id`` UUIDs.

        Only mapped ids appear in the result, so the returned dict may be
        smaller than ``source_ids``.
        """
        source_id_values = set(source_ids)
        if not source_id_values:
            return {}
        stmt = select(
            WorkspaceSyncResourceMapping.source_id,
            WorkspaceSyncResourceMapping.local_id,
        ).where(
            WorkspaceSyncResourceMapping.workspace_id == workspace_service.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
            WorkspaceSyncResourceMapping.source_id.in_(source_id_values),
        )
        return dict((await workspace_service.session.execute(stmt)).tuples().all())


class CompoundYamlAdapter(ResourceAdapter):
    """Layout ``<root>/<source_id>/<filename>``.

    Each resource owns a directory, so it can carry companion files next to its
    primary file (agent presets, skills, tables).
    """

    root: ClassVar[str]
    """Top-level repository directory for this resource type."""
    filename: ClassVar[str]
    """Primary file name inside each resource directory."""

    def source_path(self, source_id: str) -> str:
        """Build ``<root>/<source_id>/<filename>``."""
        return f"{self.root}/{source_id}/{self.filename}"

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Recover the source id from a ``<root>/<source_id>/<filename>`` path."""
        parts = path_parts(path)
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
        # Must be exactly <root>/<source_id>/<filename>.
        if len(parts) != len(root_parts) + 2:
            return None
        if parts[: len(root_parts)] != root_parts or parts[-1] != self.filename:
            return None
        return parts[-2] or None


class SingleYamlAdapter(ResourceAdapter):
    """Layout ``<root>/<source_id>.yml``.

    One flat file per resource, with no companion files (case tags, fields,
    dropdowns, durations).
    """

    root: ClassVar[str]
    """Top-level repository directory for this resource type."""

    def source_path(self, source_id: str) -> str:
        """Build ``<root>/<source_id>.yml``."""
        return f"{self.root}/{source_id}.yml"

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Recover the source id from a ``<root>/<source_id>.yml`` path."""
        parts = path_parts(path)
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
        # Must be exactly <root>/<filename>.yml.
        if len(parts) != len(root_parts) + 1:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        filename = parts[-1]
        if not filename.endswith(".yml"):
            return None
        return filename.removesuffix(".yml") or None


class EnvironmentYamlAdapter(ResourceAdapter):
    """Layout ``<root>/<environment>/<name>.yml`` (variables, secret metadata).

    The source id is the compound ``<environment>/<name>`` segment, so its
    single embedded ``/`` expands into the environment subdirectory on disk.
    """

    root: ClassVar[str]
    """Top-level repository directory for this resource type."""

    def source_path(self, source_id: str) -> str:
        """Build ``<root>/<environment>/<name>.yml`` from the compound source id."""
        # source_id is "<environment>/<name>", yielding <root>/<env>/<name>.yml.
        return f"{self.root}/{source_id}.yml"

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Recover the compound ``<environment>/<name>`` source id from a path."""
        parts = path_parts(path)
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
        # Must be exactly <root>/<environment>/<name>.yml.
        if len(parts) != len(root_parts) + 2:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        environment, filename = parts[-2], parts[-1]
        if not environment or not filename.endswith(".yml"):
            return None
        name = filename.removesuffix(".yml")
        if not name:
            return None
        return f"{environment}/{name}"


def path_parts(path: str) -> list[str]:
    """Split a repository path into its non-empty, slash-free segments."""
    return [part for part in path.strip("/").split("/") if part]


def path_segment(value: str, *, fallback: str = "resource") -> str:
    """Slugify ``value`` into a single filesystem-safe path segment.

    Replaces path separators and other unsafe characters with ``-``, trims to
    96 characters, and returns ``fallback`` when nothing usable remains.
    """
    cleaned = value.strip().replace("/", "-").replace("\\", "-")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", cleaned).strip("-._")
    return safe[:96].strip("-._") or fallback


def unique_source_id(value: str, *, reserved: set[str]) -> str:
    """Slugify ``value`` into a ``source_id`` not already in ``reserved``.

    Appends ``-2``, ``-3``, ... to break collisions. ``reserved`` is read only;
    callers add the returned id to it themselves.
    """
    base = path_segment(value)
    candidate = base
    counter = 2
    while candidate in reserved:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def environment_source_id(environment: str, name: str) -> str:
    """Build the compound ``<environment>/<name>`` source id for an env resource."""
    return f"{path_segment(environment, fallback='default')}/{path_segment(name)}"


def unique_environment_source_id(
    environment: str,
    name: str,
    *,
    reserved: set[str],
) -> str:
    """Like :func:`unique_source_id`, but for ``<environment>/<name>`` ids."""
    base = environment_source_id(environment, name)
    candidate = base
    counter = 2
    while candidate in reserved:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def sql_type(value: Any) -> SqlType:
    """Coerce a column ``type`` label into a :class:`SqlType`.

    Normalizes hyphens to underscores and upper-cases before lookup, so a label
    like ``"multi-select"`` resolves to ``SqlType.MULTI_SELECT``.
    """
    raw = str(value).replace("-", "_").upper()
    return SqlType(raw)
