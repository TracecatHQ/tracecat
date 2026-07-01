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
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, NamedTuple, Protocol, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped
from sqlalchemy.sql.base import ExecutableOption

from tracecat.auth.types import Role
from tracecat.db.models import WorkspaceSyncResourceMapping
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic
from tracecat.tables.enums import SqlType
from tracecat.tiers.enums import Entitlement
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


class VersionedSlug(NamedTuple):
    """A slug pinned to a specific resource version, e.g. ``("skill-a", 2)``."""

    slug: str
    version: int


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
    versioned_slugs: set[VersionedSlug] = field(default_factory=set)
    """Slug plus version references such as ``("skill-a", 2)``."""
    names: set[str] = field(default_factory=set)
    """Name references such as table names or environment-agnostic variable names."""


@dataclass(slots=True)
class _SourceIdAssigner:
    """Assigns projection source ids: reuse the mapped id, or mint a fresh one.

    ``reserved`` accumulates every id handed out so a freshly minted id never
    collides with an existing mapping or another row in the same projection.
    """

    mapped: dict[uuid.UUID, str]
    """Existing ``local_id`` -> ``source_id`` sync mappings."""
    reserved: set[str]
    """Source ids already taken; updated in place as ids are assigned."""

    def assign(self, local_id: uuid.UUID, name: str) -> str:
        """Reuse ``local_id``'s mapped source id, or mint one from ``name``."""
        source_id = self.mapped.get(local_id)
        if source_id is None:
            source_id = unique_source_id(name, reserved=self.reserved)
        self.reserved.add(source_id)
        return source_id

    def assign_environment(
        self, local_id: uuid.UUID, environment: str, name: str
    ) -> str:
        """Like :meth:`assign`, but mint an ``<environment>/<name>`` source id."""
        source_id = self.mapped.get(local_id)
        if source_id is None:
            source_id = unique_environment_source_id(
                environment, name, reserved=self.reserved
            )
        self.reserved.add(source_id)
        return source_id


class _WorkspaceRow(Protocol):
    """A workspace-scoped database row addressable by its public ``id``.

    The bound for :meth:`ResourceAdapter._row_by_source_id`, satisfied by every
    ``WorkspaceModel`` carrying a UUID ``id`` column (tables, variables, agent
    presets, etc.). Declared structurally because ``id`` lives on each concrete
    model rather than the shared base.
    """

    id: Mapped[uuid.UUID]
    workspace_id: Mapped[uuid.UUID]


_TEMP_NAME_PREFIX = "__tracecat_sync_tmp_"
"""Prefix for placeholder names rows are parked under during a rename swap."""


class SyncMappingService(BaseWorkspaceService):
    """Workspace service that namespaces sync resource mappings by VCS provider.

    The resource adapters read and write :class:`WorkspaceSyncResourceMapping`
    rows scoped to a provider; every service that drives them
    (``WorkspaceSyncService``, the projector, and the importer) extends this base
    and fixes the provider at construction so the active provider is part of the
    static contract instead of being duck-typed at the call site.
    """

    def __init__(
        self,
        session: AsyncSession,
        role: Role | None = None,
        *,
        mapping_provider: VcsProvider = VcsProvider.GITHUB,
    ) -> None:
        """Initialize with the provider namespace for sync resource mappings."""
        super().__init__(session=session, role=role)
        self._mapping_provider = mapping_provider

    @property
    def _mapping_provider_value(self) -> str:
        """Provider value used for workspace sync resource mappings."""
        return self._mapping_provider.value


def find_duplicates(values: Iterable[str]) -> list[str]:
    """Return the sorted distinct values that appear more than once."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def unique_temporary_name(
    row_id: uuid.UUID,
    reserved: set[str],
    *,
    prefix: str = _TEMP_NAME_PREFIX,
    max_len: int | None = None,
) -> str:
    """Mint a placeholder name for ``row_id`` not present in ``reserved``.

    Parks a row under a throwaway name during a rename swap. ``max_len`` caps the
    base length for models with short name limits. The minted name is added to
    ``reserved`` so repeated calls stay collision-free.
    """
    base = f"{prefix}{row_id.hex}"
    if max_len is not None:
        base = base[:max_len]
    candidate = base
    suffix = 1
    while candidate in reserved:
        suffix += 1
        candidate = f"{base}_{suffix}"
    reserved.add(candidate)
    return candidate


@dataclass(slots=True)
class NameSwapPlan[ModelT: _WorkspaceRow]:
    """Reconciliation plan for in-place renames within one resource type.

    A single import batch can rename rows so that two of them swap names (``a``
    becomes ``b`` while ``b`` becomes ``a``). Applying those renames one at a
    time transiently collides on the synced name -- a DB unique constraint for
    tables, presets, and case durations, or import-enforced uniqueness for
    skills -- so :meth:`ResourceAdapter.plan_name_swap` parks every changing
    mapped row under a temporary name first. The returned plan holds the mapped
    rows and the availability check the planning pass and the main import loop
    both reuse.

    Resources whose name is unique only within a scope (workspace variables and
    secret metadata, keyed by ``(environment, name)``) set ``scope_attr`` so the
    collision check and parking are confined to that scope column.
    """

    model: type[ModelT]
    """Workspace-scoped model whose synced name column is reconciled."""
    name_attr: str
    """Attribute on ``model`` holding the synced name (e.g. ``"slug"``)."""
    noun: str
    """Word for the name in messages (``"slug"`` or ``"name"``)."""
    kind_label: str
    """Resource label for messages, e.g. ``"Agent preset"``."""
    owner_label: str
    """Conflicting-resource label for messages, e.g. ``"preset"``."""
    error_cls: type[Exception]
    """Exception type raised on an unresolvable name conflict."""
    targets: Mapping[str, str]
    """Desired ``source_id`` -> name for every spec in the batch."""
    mapped_by_source_id: dict[str, ModelT]
    """Rows already mapped to a ``source_id`` in the batch."""
    source_ids_by_row_id: dict[uuid.UUID, str]
    """Reverse lookup from a mapped row id back to its ``source_id``."""
    scope_attr: str | None = None
    """Attribute scoping name uniqueness (e.g. ``"environment"``), if any."""
    target_scopes: Mapping[str, str] | None = None
    """Desired ``source_id`` -> scope value; set whenever ``scope_attr`` is."""
    availability_predicates: Sequence[Any] = ()
    """Extra predicates applied when checking whether a target name is free."""

    @property
    def column(self) -> InstrumentedAttribute[str]:
        """The model's synced name column, e.g. ``AgentPreset.slug``."""
        return getattr(self.model, self.name_attr)

    @property
    def scope_column(self) -> InstrumentedAttribute[str] | None:
        """The model's scope column, e.g. ``WorkspaceVariable.environment``."""
        return getattr(self.model, self.scope_attr) if self.scope_attr else None

    def scope_of(self, source_id: str) -> str | None:
        """The scope value ``source_id`` reconciles toward, or ``None`` if unscoped."""
        return None if self.target_scopes is None else self.target_scopes[source_id]

    async def ensure_available(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        name: str,
        row_id: uuid.UUID,
        scope: str | None = None,
    ) -> None:
        """Raise when another row owns the target identity and is not vacating it.

        ``scope`` narrows the collision to rows sharing that scope value (e.g.
        environment) when the plan is scoped.
        """
        conditions = [
            self.model.workspace_id == workspace_service.workspace_id,
            self.column == name,
            self.model.id != row_id,
            *self.availability_predicates,
        ]
        if (scope_column := self.scope_column) is not None:
            conditions.append(scope_column == scope)
        conflict_id = await workspace_service.session.scalar(
            select(self.model.id).where(*conditions)
        )
        if conflict_id is None:
            return
        # Tolerate a conflict with another mapped row that is itself moving off
        # this identity in the batch: parking frees it before we claim it.
        owner_source_id = self.source_ids_by_row_id.get(conflict_id)
        if owner_source_id is not None and (
            self.scope_of(owner_source_id),
            self.targets[owner_source_id],
        ) != (scope, name):
            return
        if self.scope_attr is not None:
            raise self.error_cls(
                f"{self.kind_label} sync source id {source_id!r} cannot use "
                f"{scope!r}/{name!r} because another {self.owner_label} "
                "already uses it."
            )
        raise self.error_cls(
            f"{self.kind_label} sync source id {source_id!r} cannot use "
            f"{self.noun} {name!r} because another {self.owner_label} already "
            f"uses that {self.noun}."
        )


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
    read_scope: ClassVar[str | None] = None
    """RBAC scope required to export or dry-run import this resource type."""
    create_scope: ClassVar[str | None] = None
    """RBAC scope required when applying an import may create this resource type."""
    update_scope: ClassVar[str | None] = None
    """RBAC scope required to apply an import for this resource type."""
    required_entitlements: ClassVar[frozenset[Entitlement]] = frozenset()
    """Organization entitlements required to sync this resource type."""
    import_identity_attrs: ClassVar[tuple[str, ...]] = ()
    """Spec attributes that form the import-time unique target identity."""
    import_identity_noun: ClassVar[str] = "identity"
    """Human-readable noun for duplicate import identity diagnostics."""

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
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project this resource type's database rows into Git specs.

        Returns an empty projection by default; projectable adapters override
        it. Workflows are projected by the sync service, not here.
        """
        return ResourceProjection(specs={}, resources=[])

    async def project_dependency_refs(
        self,
        workspace_service: SyncMappingService,
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
        workspace_service: SyncMappingService,
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

    def import_identity(self, spec: BaseModel) -> tuple[str, ...] | None:
        """Return the import-time unique target identity for ``spec``, if any."""
        if not self.import_identity_attrs:
            return None
        identity = []
        for attr in self.import_identity_attrs:
            value = getattr(spec, attr, None)
            if not isinstance(value, str):
                return None
            identity.append(value)
        return tuple(identity)

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
        workspace_service: SyncMappingService,
        *,
        model: type[_WorkspaceRow] | None = None,
        row_predicates: Sequence[Any] = (),
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
            WorkspaceSyncResourceMapping.provider
            == workspace_service._mapping_provider_value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
        )
        if model is not None:
            stmt = stmt.join(model, WorkspaceSyncResourceMapping.local_id == model.id)
            stmt = stmt.where(
                model.workspace_id == workspace_service.workspace_id,
                *row_predicates,
            )
        return dict((await workspace_service.session.execute(stmt)).tuples().all())

    async def local_id_for_source_id(
        self,
        workspace_service: SyncMappingService,
        source_id: str,
    ) -> uuid.UUID | None:
        """Resolve a single ``source_id`` to its mapped ``local_id``, if any."""
        stmt = select(WorkspaceSyncResourceMapping.local_id).where(
            WorkspaceSyncResourceMapping.workspace_id == workspace_service.workspace_id,
            WorkspaceSyncResourceMapping.provider
            == workspace_service._mapping_provider_value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
        return await workspace_service.session.scalar(stmt)

    async def local_ids_by_source_id(
        self,
        workspace_service: SyncMappingService,
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
            WorkspaceSyncResourceMapping.provider
            == workspace_service._mapping_provider_value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
            WorkspaceSyncResourceMapping.source_id.in_(source_id_values),
        )
        return dict((await workspace_service.session.execute(stmt)).tuples().all())

    async def source_id_assigner(
        self,
        workspace_service: SyncMappingService,
        *,
        model: type[_WorkspaceRow] | None = None,
        row_predicates: Sequence[Any] = (),
    ) -> _SourceIdAssigner:
        """Build a :class:`_SourceIdAssigner` seeded with this type's mappings.

        Reuses each row's already-assigned source id during projection and mints
        fresh, collision-free ids for unmapped rows.
        """
        mapped = await self.source_ids_by_local_id(
            workspace_service,
            model=model,
            row_predicates=row_predicates,
        )
        return _SourceIdAssigner(mapped=mapped, reserved=set(mapped.values()))

    async def _row_by_source_id[ModelT: _WorkspaceRow](
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        model: type[ModelT],
        options: Sequence[ExecutableOption] = (),
        row_predicates: Sequence[Any] = (),
    ) -> ModelT | None:
        """Load the ``model`` row mapped to ``source_id``, or ``None`` if unmapped.

        Resolves the sync mapping to a local id, then loads that workspace-scoped
        row. ``options`` carries loader options such as ``selectinload`` for
        adapters that need eager-loaded relationships.
        """
        local_id = await self.local_id_for_source_id(workspace_service, source_id)
        if local_id is None:
            return None
        stmt = select(model).where(
            model.workspace_id == workspace_service.workspace_id,
            model.id == local_id,
            *row_predicates,
        )
        if options:
            stmt = stmt.options(*options)
        return await workspace_service.session.scalar(stmt)

    async def plan_name_swap[ModelT: _WorkspaceRow](
        self,
        workspace_service: SyncMappingService,
        *,
        targets: Mapping[str, str],
        model: type[ModelT],
        name_column: InstrumentedAttribute[str],
        noun: str,
        kind_label: str,
        owner_label: str,
        error_cls: type[Exception] = ValueError,
        options: Sequence[ExecutableOption] = (),
        scope_column: InstrumentedAttribute[str] | None = None,
        target_scopes: Mapping[str, str] | None = None,
        temp_prefix: str = _TEMP_NAME_PREFIX,
        temp_max_len: int | None = None,
        rename: Callable[[ModelT, str], Awaitable[None]] | None = None,
        row_predicates: Sequence[Any] = (),
        availability_predicates: Sequence[Any] = (),
    ) -> NameSwapPlan[ModelT]:
        """Validate target names and park mapped rows whose names change.

        Rejects duplicate targets, loads the rows already mapped to a
        ``source_id`` in the batch, confirms each can claim its target name
        (tolerating a mapped row that is itself vacating that name), then renames
        every changing mapped row to a temporary placeholder so the later per-row
        renames cannot collide on the synced name mid-swap. ``name_column`` is the
        model's synced name column, e.g. ``AgentPreset.slug``. ``scope_column``
        (with ``target_scopes``) confines uniqueness to a second column, e.g.
        ``WorkspaceVariable.environment`` keyed by ``(environment, name)``.
        ``rename`` overrides the default in-place attribute assignment for models
        that must rename through a service.
        """
        scope_attr = scope_column.key if scope_column is not None else None
        self._reject_duplicate_targets(
            targets,
            target_scopes=target_scopes,
            scope_attr=scope_attr,
            error_cls=error_cls,
            kind_label=kind_label,
            noun=noun,
        )
        mapped: dict[str, ModelT] = {}
        for source_id in sorted(targets):
            row = await self._row_by_source_id(
                workspace_service,
                source_id=source_id,
                model=model,
                options=options,
                row_predicates=row_predicates,
            )
            if row is not None:
                mapped[source_id] = row
        plan = NameSwapPlan(
            model=model,
            name_attr=name_column.key,
            noun=noun,
            kind_label=kind_label,
            owner_label=owner_label,
            error_cls=error_cls,
            targets=targets,
            mapped_by_source_id=mapped,
            source_ids_by_row_id={
                row.id: source_id for source_id, row in mapped.items()
            },
            scope_attr=scope_attr,
            target_scopes=target_scopes,
            availability_predicates=availability_predicates,
        )
        for source_id, row in mapped.items():
            await plan.ensure_available(
                workspace_service,
                source_id=source_id,
                name=targets[source_id],
                row_id=row.id,
                scope=plan.scope_of(source_id),
            )
        await self._park_changing_names(
            workspace_service,
            plan,
            temp_prefix=temp_prefix,
            temp_max_len=temp_max_len,
            rename=rename,
        )
        return plan

    @staticmethod
    def _reject_duplicate_targets(
        targets: Mapping[str, str],
        *,
        target_scopes: Mapping[str, str] | None,
        scope_attr: str | None,
        error_cls: type[Exception],
        kind_label: str,
        noun: str,
    ) -> None:
        """Raise when two specs reconcile toward the same target identity."""
        if scope_attr is None:
            duplicates = find_duplicates(targets.values())
            if duplicates:
                raise error_cls(
                    f"{kind_label} sync specs must have unique {noun}s: "
                    + ", ".join(repr(value) for value in duplicates)
                )
            return
        if target_scopes is None:
            raise ValueError("plan_name_swap requires target_scopes with scope_column")
        seen: set[tuple[str, str]] = set()
        duplicates_scoped: set[tuple[str, str]] = set()
        for source_id in sorted(targets):
            identity = (target_scopes[source_id], targets[source_id])
            if identity in seen:
                duplicates_scoped.add(identity)
            seen.add(identity)
        if duplicates_scoped:
            raise error_cls(
                f"{kind_label} sync specs must have unique targets: "
                + ", ".join(
                    f"{scope!r}/{name!r}" for scope, name in sorted(duplicates_scoped)
                )
            )

    async def _park_changing_names[ModelT: _WorkspaceRow](
        self,
        workspace_service: SyncMappingService,
        plan: NameSwapPlan[ModelT],
        *,
        temp_prefix: str,
        temp_max_len: int | None,
        rename: Callable[[ModelT, str], Awaitable[None]] | None,
    ) -> None:
        """Rename changing mapped rows to collision-free temporary placeholders."""
        if not plan.mapped_by_source_id:
            return
        reserved = await self._reserved_names_by_scope(workspace_service, plan)
        changed = False
        for source_id, row in plan.mapped_by_source_id.items():
            scope = getattr(row, plan.scope_attr) if plan.scope_attr else None
            # Skip rows already at their target identity: nothing to free.
            if (scope, getattr(row, plan.name_attr)) == (
                plan.scope_of(source_id),
                plan.targets[source_id],
            ):
                continue
            temp_name = unique_temporary_name(
                row.id,
                reserved.setdefault(scope, set()),
                prefix=temp_prefix,
                max_len=temp_max_len,
            )
            if rename is not None:
                await rename(row, temp_name)
            else:
                setattr(row, plan.name_attr, temp_name)
                workspace_service.session.add(row)
            changed = True
        if changed:
            await workspace_service.session.flush()

    async def _reserved_names_by_scope[ModelT: _WorkspaceRow](
        self,
        workspace_service: SyncMappingService,
        plan: NameSwapPlan[ModelT],
    ) -> dict[str | None, set[str]]:
        """In-use names plus batch targets, bucketed by scope value.

        The bucket key is ``None`` for unscoped plans, so a single flat namespace
        and per-scope namespaces share one parking loop.
        """
        reserved: dict[str | None, set[str]] = {}
        scope_column = plan.scope_column
        if scope_column is None:
            existing = (
                await workspace_service.session.scalars(
                    select(plan.column).where(
                        plan.model.workspace_id == workspace_service.workspace_id,
                        *plan.availability_predicates,
                    )
                )
            ).all()
            reserved[None] = set(existing) | set(plan.targets.values())
            return reserved
        rows = (
            await workspace_service.session.execute(
                select(scope_column, plan.column).where(
                    plan.model.workspace_id == workspace_service.workspace_id,
                    *plan.availability_predicates,
                )
            )
        ).tuples()
        for scope_value, name in rows:
            reserved.setdefault(scope_value, set()).add(name)
        for source_id, name in plan.targets.items():
            reserved.setdefault(plan.scope_of(source_id), set()).add(name)
        return reserved


class DirectoryManifestAdapter(ResourceAdapter):
    """Directory layout with one primary manifest file.

    Each resource owns ``<root>/<source_id>/``. ``<filename>`` is the primary
    manifest file that anchors identity and parsing, while companion files can
    live alongside it (agent presets, skills, tables).
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


class FlatManifestAdapter(ResourceAdapter):
    """Flat layout with one manifest file per resource.

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


class EnvironmentScopedManifestAdapter(ResourceAdapter):
    """Environment-scoped layout with one manifest file per resource.

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
