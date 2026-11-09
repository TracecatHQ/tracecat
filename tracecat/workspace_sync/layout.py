"""Readable repository paths for workspace sync resources.

A resource has two separate things:

- Its identity, the source id. It is written as ``id:`` inside the resource's
  file and never changes, e.g. ``wf_4f2a...`` for a workflow.
- Its location, a folder or file name derived from the resource's current
  name, e.g. ``workflows/Triage-Alerts/definition.yml``.

Because identity lives in the file, renaming a resource only moves its file.
Pulls match resources by the ``id`` inside each file, so a moved file is the
same resource, not a delete plus a create.

Repositories written before readable paths name folders by source id
(``workflows/wf_4f2a.../definition.yml``), the "legacy id path". Those still
parse, and the next push moves them to readable paths.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from tracecat.workspace_sync.adapters import WORKSPACE_RESOURCE_ADAPTERS
from tracecat.workspace_sync.adapters.base import ResourceAdapter, path_segment
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.exceptions import PushPathConflictError
from tracecat.workspace_sync.schemas import (
    PathsByType,
    ResourcePathMap,
    WorkspaceManifest,
    WorkspaceSpec,
)

DEFAULT_ROOTS = WorkspaceManifest().resources
"""Pushes always write the default resource roots."""


def plan_resource_paths(
    spec: WorkspaceSpec,
    *,
    fixed: PathsByType | None = None,
    previous: PathsByType | None = None,
    reserved: Mapping[SyncResourceType, Iterable[str]] | None = None,
    other_source_ids: Mapping[SyncResourceType, Iterable[str]] | None = None,
) -> ResourcePathMap:
    """Choose a readable primary file path for every resource in ``spec``.

    Each resource type is planned on its own, in three passes:

    1. A resource in ``fixed`` takes that path as is. Fixed paths of resources
       not in ``spec`` are still off limits to the others.
    2. A resource whose ``previous`` path is still a readable choice for its
       current name keeps it, suffix included. So a new resource with the same
       name never pushes it aside, and it only moves when it is renamed.
    3. Every other resource, in source id order, takes its readable path. If
       that path is taken, the source id is appended: ``Triage-wf_4f2a...``.

    Two rules keep paths unambiguous:

    - Paths are compared case-insensitively, so two folders never differ only
      by case. That would collide on macOS and Windows clones.
    - No resource takes another resource's legacy id path. Package include
      patterns select resources by id path, so ``tables/Assets/**`` must only
      ever mean the table whose source id is ``Assets``.

    Args:
        spec: Resources to place.
        fixed: Paths that must be used as is, e.g. paths read from a commit.
        previous: Where each resource was last written or read. A resource's
            own previous path wins over ``reserved``.
        reserved: Paths other resources hold in the repository.
        other_source_ids: Source ids of resources outside ``spec``, whose
            legacy id paths are off limits too.

    Returns:
        Primary file path by resource type, then source id.
    """
    planned: ResourcePathMap = {}
    for adapter in WORKSPACE_RESOURCE_ADAPTERS:
        specs = adapter.specs(spec)
        if not specs:
            continue
        resource_type = adapter.resource_type
        fixed_paths = (fixed or {}).get(resource_type, {})
        id_paths: dict[str, set[str]] = {}
        for source_id in [*specs, *(other_source_ids or {}).get(resource_type, ())]:
            folded = _fold(adapter.legacy_id_path(source_id))
            id_paths.setdefault(folded, set()).add(source_id)
        planner = _TypePlanner(
            adapter,
            id_paths=id_paths,
            reserved={
                _fold(path)
                for path in [
                    *(reserved or {}).get(resource_type, ()),
                    *fixed_paths.values(),
                ]
            },
        )
        for source_id, path in fixed_paths.items():
            if source_id in specs:
                planner.claim(source_id, path)
        keys = {
            source_id: adapter.path_key(source_id, resource_spec)
            for source_id, resource_spec in specs.items()
        }
        last = (previous or {}).get(resource_type, {})
        for source_id in sorted(specs):
            path = last.get(source_id)
            # A resource's own previous path ignores ``reserved``: two
            # resources cannot share one location, so that claim is stale.
            if (
                source_id not in planner.paths
                and path is not None
                and _is_readable_choice(adapter, path, keys[source_id], source_id)
                and planner.is_free(source_id, path)
            ):
                planner.claim(source_id, path)
        for source_id in sorted(specs):
            if source_id not in planner.paths:
                planner.claim_readable(source_id, keys[source_id])
        planned[resource_type] = dict(sorted(planner.paths.items()))
    return planned


@dataclass(slots=True)
class _TypePlanner:
    """Hands out collision-free paths for one resource type."""

    adapter: ResourceAdapter
    id_paths: dict[str, set[str]]
    """Case-folded legacy id path -> the source ids it belongs to."""
    reserved: set[str]
    """Case-folded paths other resources hold."""
    claimed: set[str] = field(default_factory=set)
    """Case-folded paths handed out by this plan."""
    paths: dict[str, str] = field(default_factory=dict)
    """Chosen path by source id."""

    def is_free(self, source_id: str, path: str) -> bool:
        """Return whether ``path`` is unclaimed and not another's id path."""
        folded = _fold(path)
        if folded in self.claimed:
            return False
        owners = self.id_paths.get(folded)
        return owners is None or source_id in owners

    def claim(self, source_id: str, path: str) -> None:
        self.paths[source_id] = path
        self.claimed.add(_fold(path))

    def claim_readable(self, source_id: str, key: str) -> None:
        """Claim the first free path among ``key`` and its suffixed variants."""
        for candidate in _candidate_keys(key, source_id):
            path = self.adapter.path_for_key(candidate)
            if self.is_free(source_id, path) and _fold(path) not in self.reserved:
                self.claim(source_id, path)
                return


def _candidate_keys(key: str, source_id: str) -> Iterator[str]:
    """Yield ``key``, then ``key-<source id>``, then numbered variants."""
    yield key
    suffixed = f"{key}-{path_segment(source_id)}"
    yield suffixed
    counter = 2
    while True:
        yield f"{suffixed}-{counter}"
        counter += 1


def _is_readable_choice(
    adapter: ResourceAdapter, path: str, key: str, source_id: str
) -> bool:
    """Return whether ``path`` is one of the paths ``claim_readable`` could give.

    That is the plain readable path, or a collision-suffixed one:
    ``key-<source id>`` or ``key-<source id>-<n>``.
    """
    path_key = adapter.path_key_from_path(path, DEFAULT_ROOTS)
    if path_key is None:
        return False
    suffixed = f"{key}-{path_segment(source_id)}"
    # E.g. "Triage", "Triage-wf_b" or "Triage-wf_b-2". The counter is written
    # exactly as the planner writes it: from 2 up, no leading zero, ASCII only.
    # Case-sensitive, so a case-only rename still moves the folder.
    pattern = rf"{re.escape(key)}|{re.escape(suffixed)}(-([2-9]|[1-9][0-9]+))?"
    return re.fullmatch(pattern, path_key) is not None


def _fold(path: str) -> str:
    return path.casefold()


def plan_push_paths(
    spec: WorkspaceSpec,
    *,
    mapped: PathsByType,
    owns_repository: bool,
    fixed: PathsByType | None = None,
) -> ResourcePathMap:
    """Plan the paths a push writes, from local state alone.

    The plan depends only on the database, never on the branch, so a durable
    push computes the same files when it is prepared and when it is applied.

    Args:
        spec: Resources the push writes.
        mapped: Last known path of every mapped resource, from sync mappings.
        owns_repository: Whether the push deletes every other resource file,
            as full and package pushes do. Otherwise other resources' files
            stay, so their last known paths are off limits.
        fixed: Paths to keep as they are, such as the full projection's paths
            when a package push writes a selection of it.
    """
    # Mapped resources this push does not write.
    others: ResourcePathMap = {}
    for adapter in WORKSPACE_RESOURCE_ADAPTERS:
        pushed = adapter.specs(spec)
        others[adapter.resource_type] = {
            source_id: path
            for source_id, path in mapped.get(adapter.resource_type, {}).items()
            if source_id not in pushed
        }
    return plan_resource_paths(
        spec,
        fixed=fixed,
        previous=mapped,
        reserved=(
            None
            if owns_repository
            else {
                resource_type: held.values() for resource_type, held in others.items()
            }
        ),
        other_source_ids={
            resource_type: held.keys() for resource_type, held in others.items()
        },
    )


type ResourceLocations = dict[SyncResourceType, dict[str, list[str]]]
"""Every primary file path found for each resource, by type then source id.

Usually one path per resource. A broken repository can hold the same ``id``
in several files, for example after two unmerged renames.
"""


def partial_push_delete_roots(
    paths: PathsByType,
    base: ResourceLocations,
) -> list[str]:
    """Return the old locations a partial push deletes from its base tree.

    ``base`` maps each ``id`` found in the tree the push writes onto to the
    files declaring it. A pushed resource found anywhere other than its
    planned location moved, so that location is stale. This heals duplicates
    too: every extra copy is stale.

    Full and package pushes delete every other resource file anyway, so only
    partial pushes need this.

    Raises:
        PushPathConflictError: The push would write into a location that holds
            a different resource it does not push, such as one a teammate
            pushed that this workspace has not pulled.
    """
    stale: set[str] = set()
    conflicts: list[str] = []
    for adapter in WORKSPACE_RESOURCE_ADAPTERS:
        planned = {
            source_id: adapter.location(path)
            for source_id, path in paths.get(adapter.resource_type, {}).items()
        }
        # Case-folded location in the base tree -> the source id found there.
        holders: dict[str, str] = {}
        for source_id, found in base.get(adapter.resource_type, {}).items():
            for path in found:
                location = adapter.location(path)
                holders[_fold(location)] = source_id
                if source_id in planned and location != planned[source_id]:
                    stale.add(location)
        # Writing over a resource this push also writes is fine: its old
        # location is stale above. Any other holder would be overwritten.
        for location in sorted(planned.values()):
            holder = holders.get(_fold(location))
            if holder is not None and holder not in planned:
                conflicts.append(location)
    if conflicts:
        raise PushPathConflictError(conflicts)
    return sorted(stale)


def without_resources[T](
    by_type: Mapping[SyncResourceType, Mapping[str, T]],
    dropped: set[tuple[SyncResourceType, str]],
) -> dict[SyncResourceType, dict[str, T]]:
    """Drop ``(resource type, source id)`` entries from a per-resource map."""
    return {
        resource_type: {
            source_id: value
            for source_id, value in by_id.items()
            if (resource_type, source_id) not in dropped
        }
        for resource_type, by_id in by_type.items()
    }


@dataclass(frozen=True, slots=True)
class PathOwner:
    """The resource a repository path belongs to."""

    adapter: ResourceAdapter
    source_id: str


class ResourcePathIndex:
    """Find the resource that owns a repository path.

    Built from planned paths, plus any locations found in a base tree, so a
    file a push deletes is labelled with its real source id. Paths no
    resource claims return ``None``.
    """

    def __init__(
        self,
        paths: PathsByType,
        base: ResourceLocations | None = None,
    ) -> None:
        self._by_path: dict[str, PathOwner] = {}
        self._by_folder: dict[str, PathOwner] = {}
        for adapter in WORKSPACE_RESOURCE_ADAPTERS:
            found = (base or {}).get(adapter.resource_type, {})
            for source_id, base_paths in found.items():
                for path in base_paths:
                    self._add(PathOwner(adapter, source_id), path)
            # Planned paths are added last, so they win over the base tree.
            for source_id, path in paths.get(adapter.resource_type, {}).items():
                self._add(PathOwner(adapter, source_id), path)

    def _add(self, owner: PathOwner, primary_path: str) -> None:
        """Index a primary file, plus its folder for folder-owning types."""
        self._by_path[primary_path] = owner
        if owner.adapter.owns_directory:
            self._by_folder[owner.adapter.location(primary_path)] = owner

    def owner(self, path: str) -> PathOwner | None:
        """Return the resource that owns ``path``, or ``None``."""
        if owner := self._by_path.get(path):
            return owner
        folder = path
        while "/" in folder:
            folder = folder.rsplit("/", 1)[0]
            if owner := self._by_folder.get(folder):
                return owner
        return None
