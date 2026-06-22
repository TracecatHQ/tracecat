"""Base class and shared helpers for workspace sync resource adapters.

A :class:`ResourceAdapter` owns everything about one Git-backed workspace
resource type in a single place: how it maps to repository paths, how it parses
and serializes files, how it is projected out of the database, and how it is
imported back in. The projector, importer, and parser are thin loops that
delegate to the adapters.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import WorkspaceSyncResourceMapping
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic
from tracecat.tables.enums import SqlType
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import WorkspaceManifestResources, WorkspaceSpec

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
    resource_type: SyncResourceType
    source_id: str
    source_path: str
    local_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ImportedResource:
    resource_type: SyncResourceType
    source_id: str
    source_path: str
    local_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ResourceProjection:
    specs: dict[str, BaseModel]
    resources: list[ProjectedResource]


class ResourceAdapter(ABC):
    """One Git-backed workspace resource type.

    Subclasses declare the class-level metadata (``resource_type``,
    ``spec_attr``, ``model``) and override the behavior they support. Resources
    that are not projectable or importable (e.g. workflows, which are handled
    directly by the sync service) simply inherit the no-op defaults.
    """

    resource_type: ClassVar[SyncResourceType]
    spec_attr: ClassVar[WorkspaceSpecField]
    model: ClassVar[type[BaseModel]]

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
        """Return the source id a primary file path maps to, or ``None``."""

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map a companion file path to ``(source_id, relative_path)``."""
        return None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize companion files (skill files, table rows, ...)."""
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
    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        """Project local database state into resource specs."""
        return ResourceProjection(specs={}, resources=[])

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        """Reconcile resource specs into local database state."""
        return []

    # -- helpers ----------------------------------------------------------
    def specs(self, spec: WorkspaceSpec) -> dict[str, BaseModel]:
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
        return ImportedResource(
            resource_type=self.resource_type,
            source_id=source_id,
            source_path=self.source_path(source_id),
            local_id=local_id,
        )

    async def source_ids_by_local_id(
        self,
        ctx: BaseWorkspaceService,
    ) -> dict[uuid.UUID, str]:
        stmt = select(
            WorkspaceSyncResourceMapping.local_id,
            WorkspaceSyncResourceMapping.source_id,
        ).where(
            WorkspaceSyncResourceMapping.workspace_id == ctx.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
        )
        return dict((await ctx.session.execute(stmt)).tuples().all())

    async def local_id_for_source_id(
        self,
        ctx: BaseWorkspaceService,
        source_id: str,
    ) -> uuid.UUID | None:
        stmt = select(WorkspaceSyncResourceMapping.local_id).where(
            WorkspaceSyncResourceMapping.workspace_id == ctx.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        return await ctx.session.scalar(stmt)

    async def local_ids_by_source_id(
        self,
        ctx: BaseWorkspaceService,
        source_ids: Iterable[str],
    ) -> dict[str, uuid.UUID]:
        source_id_values = set(source_ids)
        if not source_id_values:
            return {}
        stmt = select(
            WorkspaceSyncResourceMapping.source_id,
            WorkspaceSyncResourceMapping.local_id,
        ).where(
            WorkspaceSyncResourceMapping.workspace_id == ctx.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
            WorkspaceSyncResourceMapping.source_id.in_(source_id_values),
        )
        return dict((await ctx.session.execute(stmt)).tuples().all())


class CompoundYamlAdapter(ResourceAdapter):
    """``<root>/<source_id>/<filename>`` layout (agent presets, skills, tables)."""

    root: ClassVar[str]
    filename: ClassVar[str]

    def source_path(self, source_id: str) -> str:
        return f"{self.root}/{source_id}/{self.filename}"

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        parts = path_parts(path)
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
        if len(parts) != len(root_parts) + 2:
            return None
        if parts[: len(root_parts)] != root_parts or parts[-1] != self.filename:
            return None
        return parts[-2] or None


class SingleYamlAdapter(ResourceAdapter):
    """``<root>/<source_id>.yml`` layout (case tags, fields, dropdowns, durations)."""

    root: ClassVar[str]

    def source_path(self, source_id: str) -> str:
        return f"{self.root}/{source_id}.yml"

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        parts = path_parts(path)
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
        if len(parts) != len(root_parts) + 1:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        filename = parts[-1]
        if not filename.endswith(".yml"):
            return None
        return filename.removesuffix(".yml") or None


class EnvironmentYamlAdapter(ResourceAdapter):
    """``<root>/<environment>/<name>.yml`` layout (variables, secret metadata).

    The source id is the compound ``<environment>/<name>`` segment.
    """

    root: ClassVar[str]

    def source_path(self, source_id: str) -> str:
        return f"{self.root}/{source_id}.yml"

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        parts = path_parts(path)
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
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
    return [part for part in path.strip("/").split("/") if part]


def path_segment(value: str, *, fallback: str = "resource") -> str:
    cleaned = value.strip().replace("/", "-").replace("\\", "-")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", cleaned).strip("-._")
    return safe[:96].strip("-._") or fallback


def unique_source_id(value: str, *, reserved: set[str]) -> str:
    base = path_segment(value)
    candidate = base
    counter = 2
    while candidate in reserved:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def environment_source_id(environment: str, name: str) -> str:
    return f"{path_segment(environment, fallback='default')}/{path_segment(name)}"


def unique_environment_source_id(
    environment: str,
    name: str,
    *,
    reserved: set[str],
) -> str:
    base = environment_source_id(environment, name)
    candidate = base
    counter = 2
    while candidate in reserved:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def sql_type(value: Any) -> SqlType:
    raw = str(value).replace("-", "_").upper()
    return SqlType(raw)
