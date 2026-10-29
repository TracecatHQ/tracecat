"""Managed resources package mode for the source workspace's Git sync.

When the bound workspace is the configured managed resources source, a push
does not export the whole workspace or an ad hoc selection. It exports exactly
the resources the repository's include file names, plus their dependencies,
and deletes every other resource file. The include file and the package
metadata in ``tracecat.json`` are written in the same commit, so the
selection and the contents never drift.

Example: an include file with ``workflows/triage/**`` exports the ``triage``
workflow as *explicit*, and the table and child workflow it uses as
*implied*.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from sqlalchemy import select, tuple_

from tracecat.agent.skill.frontmatter import parse_skill_markdown
from tracecat.db.models import (
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.managed_resources.include import (
    PACKAGE_INCLUDE_FILENAME,
    PackageInclude,
    PackageResourceKey,
    matched_resources,
    parse_package_include,
    unmatched_patterns,
)
from tracecat.registry.actions.schemas import RegistryActionImplValidator
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.workspace_sync.adapters import (
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    RESOURCE_ADAPTERS_BY_TYPE,
    WORKFLOW_RESOURCE_ADAPTER,
)
from tracecat.workspace_sync.adapters.base import DirectoryManifestAdapter
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    PackageRegistryRequirement,
    PackageResourceEntry,
    PackageResourceStatus,
    WorkspaceManifestResources,
    WorkspacePackageManifest,
    WorkspacePackageRead,
    WorkspacePackageResource,
    WorkspaceProjection,
    WorkspaceSpec,
)
from tracecat.workspace_sync.types import SyncMappingTarget

if TYPE_CHECKING:
    from tracecat.workspace_sync.service import WorkspaceSyncService


@dataclass(frozen=True, slots=True)
class PackageResolution:
    """An include file resolved against the source workspace."""

    full: WorkspaceProjection
    """Projection of every syncable resource in the workspace."""
    projection: WorkspaceProjection
    """Projection of the package: explicit resources and their closure."""
    explicit: frozenset[PackageResourceKey]
    """Resources an include pattern matches."""
    implied: frozenset[PackageResourceKey]
    """Resources pulled in only as dependencies."""
    warnings: tuple[str, ...]
    """Human-readable notes, such as patterns that match nothing."""


@dataclass(frozen=True, slots=True)
class PackagePushProjection:
    """Everything a package-mode push writes."""

    projection: WorkspaceProjection
    """Package projection whose files include the include file and metadata."""
    delete_roots: tuple[str, ...]
    """Every resource root: the repository must contain only the package."""
    include_text: str
    """Include file contents written with the push."""


def include_reader(text: str | None) -> Callable[[], Awaitable[str | None]]:
    """Return a reader that yields include file text already in hand."""

    async def read() -> str | None:
        return text

    return read


def paths_by_resource(
    projection: WorkspaceProjection,
) -> dict[PackageResourceKey, list[str]]:
    """Group a projection's files by the resource that owns each one.

    The manifest and the include file belong to no resource and are skipped.
    """
    roots = projection.manifest.resources
    grouped: dict[PackageResourceKey, list[str]] = defaultdict(list)
    for path in sorted(projection.files):
        if path in (MANIFEST_FILENAME, PACKAGE_INCLUDE_FILENAME):
            continue
        if (key := resource_key_from_path(path, roots=roots)) is not None:
            grouped[key].append(path)
    return dict(grouped)


def resource_key_from_path(
    path: str, *, roots: WorkspaceManifestResources
) -> PackageResourceKey | None:
    """Resolve an export path to the resource that owns it, or ``None``."""
    for adapter in (WORKFLOW_RESOURCE_ADAPTER, *NON_WORKFLOW_RESOURCE_ADAPTERS):
        if source_id := adapter.source_id_from_path(path, roots):
            return PackageResourceKey(adapter.resource_type, source_id)
        if extra := adapter.extra_path_from_path(path, roots):
            return PackageResourceKey(adapter.resource_type, extra[0])
    return None


def resource_keys(projection: WorkspaceProjection) -> set[PackageResourceKey]:
    """Return every resource in a projection's spec."""
    keys = {
        PackageResourceKey(SyncResourceType.WORKFLOW, source_id)
        for source_id in projection.spec.workflows
    }
    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        keys.update(
            PackageResourceKey(adapter.resource_type, source_id)
            for source_id in adapter.specs(projection.spec)
        )
    return keys


def include_pattern_for(key: PackageResourceKey) -> str:
    """Return the include pattern the picker writes for one resource.

    Directory layouts select the whole resource directory, so companion files
    such as skill files come along. Flat layouts name the one file.
    """
    adapter = RESOURCE_ADAPTERS_BY_TYPE[key.resource_type]
    path = adapter.source_path(key.source_id)
    if key.resource_type == SyncResourceType.WORKFLOW or isinstance(
        adapter, DirectoryManifestAdapter
    ):
        return f"{path.rsplit('/', 1)[0]}/**"
    return path


def _selection(
    targets: Iterable[SyncMappingTarget], keys: set[PackageResourceKey]
) -> dict[SyncResourceType, set[uuid.UUID]]:
    selection: dict[SyncResourceType, set[uuid.UUID]] = defaultdict(set)
    for target in targets:
        key = PackageResourceKey(
            SyncResourceType(target.resource_type), target.source_id
        )
        if key in keys:
            selection[key.resource_type].add(target.local_id)
    return dict(selection)


async def resolve_package(
    service: WorkspaceSyncService,
    include: PackageInclude,
    *,
    create_missing_mappings: bool,
) -> PackageResolution:
    """Resolve an include file to the explicit and implied package resources.

    Projects the whole workspace to learn every export path, matches the
    include patterns against those paths, then projects the matched resources
    with their dependency closure.

    Args:
        service: Sync service bound to the source workspace.
        include: The parsed include file.
        create_missing_mappings: Whether unmapped resources get persistent
            Git source ids. Pushes pass ``True`` so paths stay stable.
    """
    full = await service.project_workspace(
        create_missing_mappings=create_missing_mappings
    )
    explicit = matched_resources(include, paths_by_resource(full))
    warnings = tuple(
        f"Pattern {pattern!r} matches no resource"
        for pattern in unmatched_patterns(
            include,
            (
                path
                for path in full.files
                if path not in (MANIFEST_FILENAME, PACKAGE_INCLUDE_FILENAME)
            ),
        )
    )
    if not explicit:
        empty = await service.empty_projection()
        return PackageResolution(
            full=full,
            projection=empty,
            explicit=frozenset(),
            implied=frozenset(),
            warnings=warnings,
        )
    projection = await service.project_workspace(
        resource_ids=_selection(full.mapping_targets, explicit),
        create_missing_mappings=create_missing_mappings,
        validate_export=True,
    )
    implied = resource_keys(projection) - explicit
    return PackageResolution(
        full=full,
        projection=projection,
        explicit=frozenset(explicit),
        implied=frozenset(implied),
        warnings=warnings,
    )


async def implied_by(
    service: WorkspaceSyncService, resolution: PackageResolution
) -> dict[PackageResourceKey, set[PackageResourceKey]]:
    """Return, for each implied resource, the explicit resources that need it.

    Projects each explicit resource's closure on its own, so it costs one
    projection per explicit resource. Only the picker calls it.
    """
    needed_by: dict[PackageResourceKey, set[PackageResourceKey]] = defaultdict(set)
    for root in sorted(
        resolution.explicit, key=lambda key: (key.resource_type, key.source_id)
    ):
        closure = await service.project_workspace(
            resource_ids=_selection(resolution.full.mapping_targets, {root}),
            create_missing_mappings=False,
        )
        for key in resource_keys(closure) & resolution.implied:
            needed_by[key].add(root)
    return dict(needed_by)


async def package_push_projection(
    service: WorkspaceSyncService,
    *,
    include_text: str,
    create_missing_mappings: bool,
) -> PackagePushProjection:
    """Build the files a package-mode push writes.

    Raises:
        PackageIncludeError: The include file has an invalid pattern.
        TracecatValidationError: A packaged custom action cannot be traced
            to a registry repository version.
    """
    include = parse_package_include(include_text)
    resolution = await resolve_package(
        service, include, create_missing_mappings=create_missing_mappings
    )
    projection = resolution.projection
    registry = await package_registry_requirements(
        service, projection.spec, organization_id=service.organization_id
    )
    manifest = projection.manifest.model_copy(
        update={
            "package": WorkspacePackageManifest(
                explicit=_entries(resolution.explicit),
                implied=_entries(resolution.implied),
                registry=registry,
            )
        }
    )
    files = service.files_from_spec(manifest=manifest, spec=projection.spec)
    files[PACKAGE_INCLUDE_FILENAME] = include_text
    return PackagePushProjection(
        projection=projection.model_copy(update={"manifest": manifest, "files": files}),
        delete_roots=await service.entitled_resource_roots(manifest.resources),
        include_text=include_text,
    )


def _entries(keys: Iterable[PackageResourceKey]) -> list[PackageResourceEntry]:
    return [
        PackageResourceEntry(resource_type=key.resource_type, source_id=key.source_id)
        for key in sorted(keys, key=lambda key: (key.resource_type, key.source_id))
    ]


def packaged_action_names(spec: WorkspaceSpec) -> set[str]:
    """Return every registry action name the packaged workflows and agents use."""
    names = {
        statement.action
        for workflow in spec.workflows.values()
        for statement in workflow.definition.actions
    }
    names.update(
        action for preset in spec.agent_presets.values() for action in preset.actions
    )
    # Skills declare registry tools in SKILL.md frontmatter; the target org
    # must have them installed or the skill import is rejected.
    for skill in spec.skills.values():
        names.update(_declared_registry_tools(skill.file_contents.get("SKILL.md")))
    return names


def _declared_registry_tools(skill_markdown: str | None) -> list[str]:
    if not skill_markdown:
        return []
    try:
        frontmatter = parse_skill_markdown(skill_markdown)
    except (ValueError, yaml.YAMLError):
        # The skill adapter reports a malformed SKILL.md on its own.
        return []
    if frontmatter is None:
        return []
    return [tool for tool in frontmatter.metadata.tools if not tool.startswith("mcp.")]


async def _with_template_steps(
    service: WorkspaceSyncService, names: set[str], *, organization_id: uuid.UUID
) -> set[str]:
    """Add the step actions of every custom template the names reach.

    A template can call an action from another registry, and rollout
    validation resolves template steps, so that registry is needed too.
    Platform templates only call platform actions, so only the organization's
    current registry versions are walked.
    """
    manifests = [
        RegistryVersionManifest.model_validate(manifest)
        for manifest in await service.session.scalars(
            select(RegistryVersion.manifest)
            .join(
                RegistryRepository,
                RegistryRepository.current_version_id == RegistryVersion.id,
            )
            .where(
                RegistryRepository.organization_id == organization_id,
                RegistryRepository.origin != DEFAULT_REGISTRY_ORIGIN,
            )
        )
    ]
    found = set(names)
    queue = deque(sorted(names))
    while queue:
        name = queue.popleft()
        for manifest in manifests:
            if (action := manifest.actions.get(name)) is None:
                continue
            impl = RegistryActionImplValidator.validate_python(action.implementation)
            if impl.type != "template":
                continue
            for step in impl.template_action.definition.steps:
                if step.action not in found:
                    found.add(step.action)
                    queue.append(step.action)
    return found


async def package_registry_requirements(
    service: WorkspaceSyncService,
    spec: WorkspaceSpec,
    *,
    organization_id: uuid.UUID,
) -> list[PackageRegistryRequirement]:
    """Record the custom registry versions the packaged actions resolve to.

    Only organization registry actions count. Built-in platform actions exist
    in every organization and need nothing.

    Raises:
        TracecatValidationError: A custom action's repository has no current
            version, so the package cannot say what to install.
    """
    qualified = sorted(
        await _with_template_steps(
            service, packaged_action_names(spec), organization_id=organization_id
        )
    )
    pairs = [tuple(name.rsplit(".", 1)) for name in qualified if "." in name]
    if not pairs:
        return []
    # Repositories synced through the registry API record their actions in
    # the current version's index, not as RegistryAction rows.
    indexed = (
        (
            await service.session.execute(
                select(
                    RegistryIndex.namespace,
                    RegistryIndex.name,
                    RegistryRepository.origin,
                    RegistryVersion.version,
                    RegistryVersion.commit_sha,
                )
                .join(
                    RegistryVersion,
                    RegistryVersion.id == RegistryIndex.registry_version_id,
                )
                .join(
                    RegistryRepository,
                    RegistryRepository.current_version_id == RegistryVersion.id,
                )
                .where(
                    RegistryRepository.organization_id == organization_id,
                    RegistryRepository.origin != DEFAULT_REGISTRY_ORIGIN,
                    tuple_(RegistryIndex.namespace, RegistryIndex.name).in_(pairs),
                )
            )
        )
        .tuples()
        .all()
    )
    found = {(namespace, name) for namespace, name, *_ in indexed}
    # Older repositories only have RegistryAction rows. They also show a
    # custom action whose repository has no current version. Anything in
    # neither place is a platform action, which every organization has; the
    # source's export validation already rejected unknown actions.
    legacy: list[tuple[str, str, str, str | None, str | None]] = []
    if remaining := [pair for pair in pairs if pair not in found]:
        legacy = list(
            (
                await service.session.execute(
                    select(
                        RegistryAction.namespace,
                        RegistryAction.name,
                        RegistryRepository.origin,
                        RegistryVersion.version,
                        RegistryVersion.commit_sha,
                    )
                    .join(
                        RegistryRepository,
                        RegistryRepository.id == RegistryAction.repository_id,
                    )
                    .outerjoin(
                        RegistryVersion,
                        RegistryVersion.id == RegistryRepository.current_version_id,
                    )
                    .where(
                        RegistryAction.organization_id == organization_id,
                        RegistryRepository.origin != DEFAULT_REGISTRY_ORIGIN,
                        tuple_(RegistryAction.namespace, RegistryAction.name).in_(
                            remaining
                        ),
                    )
                )
            )
            .tuples()
            .all()
        )
    rows = [*indexed, *legacy]
    by_origin: dict[str, PackageRegistryRequirement] = {}
    untraceable: list[str] = []
    for namespace, name, origin, version, commit_sha in rows:
        action = f"{namespace}.{name}"
        if version is None:
            untraceable.append(action)
            continue
        requirement = by_origin.setdefault(
            origin,
            PackageRegistryRequirement(
                origin=origin, version=version, commit_sha=commit_sha
            ),
        )
        requirement.actions.append(action)
    if untraceable:
        raise TracecatValidationError(
            "These packaged actions belong to a registry repository with no "
            "current version, so the package cannot ship them: "
            + ", ".join(sorted(untraceable))
        )
    for requirement in by_origin.values():
        requirement.actions.sort()
    return [by_origin[origin] for origin in sorted(by_origin)]


async def read_package(
    service: WorkspaceSyncService, *, include_text: str, ref: str | None
) -> WorkspacePackageRead:
    """Describe every source resource with its package status for the picker.

    Raises:
        PackageIncludeError: The include file has an invalid pattern.
    """
    include = parse_package_include(include_text)
    resolution = await resolve_package(service, include, create_missing_mappings=False)
    # The picker lists every resource by name, so hold the caller to the read
    # scopes an export of the same resources needs.
    service._require_projected_export_scopes(resolution.full.spec)
    needed_by = await implied_by(service, resolution)
    names = _resource_names(resolution.full)
    resources: list[WorkspacePackageResource] = []
    for target in sorted(
        resolution.full.mapping_targets,
        key=lambda target: (target.resource_type, target.source_id),
    ):
        key = PackageResourceKey(
            SyncResourceType(target.resource_type), target.source_id
        )
        if key in resolution.explicit:
            status: PackageResourceStatus = "explicit"
        elif key in resolution.implied:
            status = "implied"
        else:
            status = "excluded"
        resources.append(
            WorkspacePackageResource(
                resource_type=key.resource_type,
                source_id=key.source_id,
                local_id=target.local_id,
                name=names.get(key, key.source_id),
                path=target.source_path,
                pattern=include_pattern_for(key),
                status=status,
                implied_by=sorted(
                    names.get(root, root.source_id) for root in needed_by.get(key, ())
                ),
            )
        )
    return WorkspacePackageRead(
        is_source=True,
        ref=ref,
        patterns=list(include.patterns),
        resources=resources,
        warnings=list(resolution.warnings),
    )


def _resource_names(projection: WorkspaceProjection) -> dict[PackageResourceKey, str]:
    names: dict[PackageResourceKey, str] = {
        PackageResourceKey(SyncResourceType.WORKFLOW, source_id): (
            spec.definition.title or spec.alias or source_id
        )
        for source_id, spec in projection.spec.workflows.items()
    }
    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        for source_id, spec in adapter.specs(projection.spec).items():
            names[PackageResourceKey(adapter.resource_type, source_id)] = (
                adapter.display_name(spec) or source_id
            )
    return names
