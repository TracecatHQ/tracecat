"""Generic resource parsing and validation for workspace sync."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from typing import Any, NamedTuple

import yaml
from pydantic import BaseModel, ValidationError

from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.expressions import patterns
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PullDiagnostic, serializable_validation_errors
from tracecat.workspace_sync.adapters import (
    AGENT_PRESET_RESOURCE_ADAPTER,
    BUNDLED_RESOURCE_ADAPTERS,
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    WORKFLOW_RESOURCE_ADAPTER,
    WORKSPACE_RESOURCE_ADAPTERS,
    workspace_spec_from_maps,
)
from tracecat.workspace_sync.adapters.base import (
    ResourceAdapter,
    VersionedSlug,
)
from tracecat.workspace_sync.adapters.bundled import (
    BundledManifestAdapter,
    BundleFormatError,
    bundle_entry_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.exceptions import (
    PushBundleUnreadableError,
    PushNameConflictError,
)
from tracecat.workspace_sync.layout import (
    DEFAULT_ROOTS,
    ResourcePathIndex,
    normalized_path_roots,
    path_is_under_roots,
    plan_resource_paths,
)
from tracecat.workspace_sync.schemas import (
    WorkspaceManifest,
    WorkspaceSpec,
    normalize_workflow_source_id,
)
from tracecat.workspace_sync.serialization import serialize_yaml_model
from tracecat.workspace_sync.types import (
    ParsedWorkspaceFiles,
    PathsByType,
    ResourceLocations,
    ResourcePathMap,
)
from tracecat.workspace_sync.workflow import (
    is_current_workflow_spec,
    parse_workflow_spec,
    serialize_workflow_spec,
)


def parse_workspace_spec_files(
    files: dict[str, str],
    *,
    manifest: WorkspaceManifest,
) -> ParsedWorkspaceFiles:
    """Parse manifest-declared resource files into a workspace spec.

    A resource's identity is the ``id`` written inside its primary file, not
    its folder or file name, so both readable paths
    (``workflows/Triage/definition.yml``) and legacy id paths
    (``workflows/wf_abc/definition.yml``) parse the same way. A file without
    an ``id`` falls back to its folder or file name, as older files did.

    Primary files are parsed first. Companion files, such as skill files, are
    then attached to the resource whose folder they sit in.
    """
    diagnostics: list[PullDiagnostic] = []
    roots = manifest.resources

    specs_by_attr: dict[str, dict[str, BaseModel]] = {
        WORKFLOW_RESOURCE_ADAPTER.spec_attr: {},
        **{adapter.spec_attr: {} for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS},
    }
    paths: ResourcePathMap = {}
    companion_files: list[tuple[ResourceAdapter, str, str, str]] = []

    # Bundles are recorded before per-resource files, so the newer format
    # wins over a leftover per-resource copy of the same id.
    for adapter in BUNDLED_RESOURCE_ADAPTERS:
        path = adapter.bundle_path_in(roots)
        if (content := files.get(path)) is None:
            continue
        for spec in _parse_bundle(adapter, path, content, diagnostics):
            _record_resource(
                adapter,
                path=path,
                spec=spec,
                destination=specs_by_attr[adapter.spec_attr],
                paths=paths,
                diagnostics=diagnostics,
            )

    # Bundle files match no adapter's ``path_key_from_path``, so they are
    # skipped here.
    for path, content in sorted(files.items()):
        if workflow_key := WORKFLOW_RESOURCE_ADAPTER.path_key_from_path(path, roots):
            workflow, diagnostic = parse_workflow_spec(
                path, content, path_key=workflow_key
            )
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            elif workflow is not None:
                _record_resource(
                    WORKFLOW_RESOURCE_ADAPTER,
                    path=path,
                    spec=workflow,
                    destination=specs_by_attr[WORKFLOW_RESOURCE_ADAPTER.spec_attr],
                    paths=paths,
                    diagnostics=diagnostics,
                )
            continue

        for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
            if path_key := adapter.path_key_from_path(path, roots):
                spec = _parse_yaml_resource(
                    path,
                    content,
                    default_source_id=path_key,
                    model=adapter.model,
                    diagnostics=diagnostics,
                )
                if spec is not None:
                    _record_resource(
                        adapter,
                        path=path,
                        spec=spec,
                        destination=specs_by_attr[adapter.spec_attr],
                        paths=paths,
                        diagnostics=diagnostics,
                    )
                break

            if relpath := adapter.companion_relpath(path, roots):
                companion_files.append((adapter, path, relpath, content))
                break

    index = ResourcePathIndex(paths)
    extra_files: dict[SyncResourceType, dict[tuple[str, str], str]] = defaultdict(dict)
    for adapter, path, relpath, content in companion_files:
        # A companion file without a parsed primary file has nothing to attach
        # to; the primary file's own diagnostic explains why.
        owner = index.owner(path)
        if owner is not None and owner.adapter is adapter:
            extra_files[adapter.resource_type][(owner.source_id, relpath)] = content

    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        specs_by_attr[adapter.spec_attr] = adapter.attach_extra_files(
            specs_by_attr[adapter.spec_attr],
            extra_files.get(adapter.resource_type, {}),
            diagnostics,
            paths=paths,
        )

    spec = workspace_spec_from_maps(specs_by_attr)
    diagnostics.extend(validate_workspace_dependencies(spec, paths=paths))
    return ParsedWorkspaceFiles(spec=spec, diagnostics=diagnostics, paths=paths)


def locate_resources(files: Mapping[str, str]) -> ResourceLocations:
    """Find every file each resource lives in, by the ``id`` inside it.

    A push compares this with the paths it plans, to delete a moved
    resource's old files and to avoid writing over another resource. It only
    reads ids, so it follows the same identity rules as
    :func:`parse_workspace_spec_files` without validating whole files.
    Pushes write the default roots, so only those are searched.
    """
    locations: ResourceLocations = {}
    for path, content in sorted(files.items()):
        if (bundled := bundled_adapter_for(path)) is not None:
            by_id = locations.setdefault(bundled.resource_type, {})
            for entry in bundled.readable_entries(content):
                if source_id := bundle_entry_id(entry):
                    by_id.setdefault(source_id, []).append(path)
            continue
        for adapter in WORKSPACE_RESOURCE_ADAPTERS:
            if (path_key := adapter.path_key_from_path(path, DEFAULT_ROOTS)) is None:
                continue
            source_id = _declared_source_id(adapter, content, default=path_key)
            by_id = locations.setdefault(adapter.resource_type, {})
            by_id.setdefault(source_id, []).append(path)
            break
    return locations


def primary_resource_filter(spec: WorkspaceSpec) -> Callable[[str], bool]:
    """Return a filter for primary files of the resource types in ``spec``.

    A push that writes only some types reads only those from its base tree.
    """
    adapters = [
        adapter for adapter in WORKSPACE_RESOURCE_ADAPTERS if adapter.specs(spec)
    ]
    return lambda path: is_primary_resource_path(path, adapters)


def is_primary_resource_path(
    path: str,
    adapters: Iterable[ResourceAdapter] = WORKSPACE_RESOURCE_ADAPTERS,
) -> bool:
    """Return whether ``path`` is a primary file of one of ``adapters``' types.

    A push reads only these from its base tree: their ``id`` is all
    :func:`locate_resources` needs, and companion files can be many.
    """
    bundled = bundled_adapter_for(path)
    return any(
        adapter is bundled
        or adapter.path_key_from_path(path, DEFAULT_ROOTS) is not None
        for adapter in adapters
    )


def bundled_adapter_for(path: str) -> BundledManifestAdapter | None:
    """Return the bundled type whose bundle file ``path`` is, under default roots.

    Pushes always write the default roots. Code reading a snapshot under
    other roots looks up the paths that snapshot recorded instead.
    """
    for adapter in BUNDLED_RESOURCE_ADAPTERS:
        if path == adapter.bundle_path:
            return adapter
    return None


def merge_base_bundles(
    files: Mapping[str, str],
    base_files: Mapping[str, str],
    *,
    owned_roots: Sequence[str],
    base_paths: Collection[str],
) -> dict[str, str]:
    """Keep the base tree's other bundled resources in the bundles a push writes.

    A partial push writes only some resources of a bundled type, but the
    bundle file holds all of them. Entries in the base bundle that the push
    does not write are copied over, so pushing one tag never drops another.
    A push that owns the bundle's folder, such as a full push, replaces the
    bundle instead: resources it does not write are meant to be deleted.

    Args:
        files: What the push writes.
        base_files: Text contents read from the base tree.
        owned_roots: Roots the push deletes stale files under.
        base_paths: Every file in the base tree, or empty when unknown. A
            bundle listed here but missing from ``base_files`` could not be
            read as text.

    Raises:
        PushBundleUnreadableError: The base bundle is not a valid bundle file,
            so its other entries cannot be kept.
        PushNameConflictError: A resource the push keeps has the same name as
            a pushed one, such as a tag a teammate pushed that was never
            pulled here.
    """
    merged = dict(files)
    roots = normalized_path_roots(owned_roots)
    for adapter in BUNDLED_RESOURCE_ADAPTERS:
        path = adapter.bundle_path
        pushed = files.get(path)
        if pushed is None or path_is_under_roots(path, roots):
            continue
        base = base_files.get(path)
        if base is None and path in base_paths:
            raise PushBundleUnreadableError(path)
        try:
            base_entries = [] if base is None else adapter.load_bundle(base)
        except BundleFormatError as e:
            raise PushBundleUnreadableError(path) from e
        pushed_entries = adapter.load_bundle(pushed)
        pushed_ids = {bundle_entry_id(entry) for entry in pushed_entries}
        kept = [
            entry for entry in base_entries if bundle_entry_id(entry) not in pushed_ids
        ]
        if clashes := _name_clashes(
            adapter,
            pushed_entries=pushed_entries,
            base_entries=base_entries,
            base_files=base_files,
        ):
            raise PushNameConflictError(clashes)
        if kept:
            merged[path] = adapter.dump_bundle([*pushed_entries, *kept])
    return merged


def _name_clashes(
    adapter: BundledManifestAdapter,
    *,
    pushed_entries: list[Any],
    base_entries: list[Any],
    base_files: Mapping[str, str],
) -> list[str]:
    """Return the resources a push keeps whose name a pushed resource takes.

    The next pull would fail on the duplicate name. Separate files used to
    conflict on path instead. Kept resources are the bundle's other entries,
    plus files from before bundling whose id is neither pushed nor in the
    bundle (a pull reads the bundle copy over a leftover file). Each clash is
    labelled ``<path> (<name>)``.
    """
    pushed_ids = {bundle_entry_id(entry) for entry in pushed_entries}
    bundled_ids = {bundle_entry_id(entry) for entry in base_entries}
    taken = {adapter.entry_identity(entry) for entry in pushed_entries} - {None}
    others: list[tuple[str, tuple[str, ...] | None]] = [
        (adapter.bundle_path, adapter.entry_identity(entry))
        for entry in base_entries
        if bundle_entry_id(entry) not in pushed_ids
    ]
    for path, content in sorted(base_files.items()):
        if (path_key := adapter.path_key_from_path(path, DEFAULT_ROOTS)) is None:
            continue
        spec = _parse_yaml_resource(
            path,
            content,
            default_source_id=path_key,
            model=adapter.model,
            diagnostics=[],
        )
        if spec is not None and _spec_id(spec) not in pushed_ids | bundled_ids:
            others.append((path, adapter.import_identity(spec)))
    clashes: set[str] = set()
    for path, identity in others:
        if identity is not None and identity in taken:
            clashes.add(f"{path} ({'/'.join(identity)})")
    return sorted(clashes)


def _parse_bundle(
    adapter: BundledManifestAdapter,
    path: str,
    content: str,
    diagnostics: list[PullDiagnostic],
) -> list[BaseModel]:
    """Parse every entry of a bundle file, recording a diagnostic per bad one."""
    try:
        entries = adapter.load_bundle(content)
    except BundleFormatError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=None,
                error_type="parse",
                message=str(e),
                details={},
            )
        )
        return []
    specs: list[BaseModel] = []
    for index, entry in enumerate(entries):
        try:
            specs.append(adapter.model.model_validate(entry))
        except ValidationError as e:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=_resource_title(
                        entry if isinstance(entry, dict) else None
                    ),
                    error_type="validation",
                    message=f"Validation error in entry {index}: {e}",
                    details={
                        "entry_index": index,
                        "validation_errors": serializable_validation_errors(e.errors()),
                    },
                )
            )
    return specs


def _declared_source_id(adapter: ResourceAdapter, content: str, *, default: str) -> str:
    """Return the source id the parser gives a resource file, or ``default``.

    ``default`` is the file's folder or file name. The parser uses it for a
    file without an ``id``, and for a legacy-format workflow. Only workflow
    ids are normalized by their spec model.
    """
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError:
        return default
    if not isinstance(raw, dict):
        return default
    is_workflow = adapter is WORKFLOW_RESOURCE_ADAPTER
    if is_workflow and not is_current_workflow_spec(raw):
        return default
    declared = raw.get("id", default)
    if not isinstance(declared, str):
        # The parser rejects this file; any stable key will do here.
        return default
    return normalize_workflow_source_id(declared) if is_workflow else declared


def _spec_id(spec: BaseModel) -> str:
    """Return a resource spec's source id."""
    # Every resource spec model declares ``id: str``; BaseModel does not.
    source_id = getattr(spec, "id", None)
    if not isinstance(source_id, str):
        raise TypeError(f"{type(spec).__name__} has no source id")
    return source_id


def _record_resource(
    adapter: ResourceAdapter,
    *,
    path: str,
    spec: BaseModel,
    destination: dict[str, BaseModel],
    paths: ResourcePathMap,
    diagnostics: list[PullDiagnostic],
) -> None:
    """Store a parsed resource under its source id, rejecting duplicate ids.

    Two files with the same ``id`` would claim one resource. Files are visited
    bundles first, then in path order, so the first one wins and the second
    gets a diagnostic.
    """
    source_id = _spec_id(spec)
    type_paths = paths.setdefault(adapter.resource_type, {})
    if (existing_path := type_paths.get(source_id)) is not None:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=adapter.display_name(spec),
                error_type="validation",
                message=(
                    f"Duplicate {adapter.resource_type.value} id {source_id!r}: "
                    f"already declared by {existing_path}. This can happen when "
                    "two branches rename the same resource. Delete one copy, or "
                    "push this resource from Tracecat to remove the extra copy."
                ),
                details={
                    "source_id": source_id,
                    "paths": [existing_path, path],
                },
            )
        )
        return
    destination[source_id] = spec
    type_paths[source_id] = path


def serialize_workspace_spec_files(
    *,
    manifest: WorkspaceManifest,
    spec: WorkspaceSpec,
    manifest_filename: str,
    manifest_serializer: Callable[[WorkspaceManifest], str],
    paths: PathsByType | None = None,
) -> dict[str, str]:
    """Serialize a workspace spec into its full set of repository files.

    Writes the manifest, each workflow definition, and every non-workflow
    resource's primary file plus companion files, returning a path-sorted map of
    repository path to file content. Resources in ``paths`` are written there;
    the rest get readable paths from :func:`plan_resource_paths`.
    """
    planned = plan_resource_paths(spec, fixed=paths)
    files = {manifest_filename: manifest_serializer(manifest)}
    for source_id, workflow_spec in sorted(spec.workflows.items()):
        files[WORKFLOW_RESOURCE_ADAPTER.path_in(planned, source_id)] = (
            serialize_workflow_spec(workflow_spec)
        )
    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        if isinstance(adapter, BundledManifestAdapter):
            files.update(_serialize_bundled(adapter, adapter.specs(spec), planned))
            continue
        for source_id, resource_spec in sorted(adapter.specs(spec).items()):
            primary_path = adapter.path_in(planned, source_id)
            files[primary_path] = serialize_yaml_model(resource_spec)
            files.update(adapter.serialize_extra_files(primary_path, resource_spec))
    return dict(sorted(files.items()))


def _serialize_bundled(
    adapter: BundledManifestAdapter,
    specs: Mapping[str, BaseModel],
    planned: PathsByType,
) -> dict[str, str]:
    """Serialize a bundled type: one bundle file, plus any per-resource files.

    Per-resource files appear only when ``planned`` keeps paths read from a
    repository written before bundling, such as in a pull preview.
    """
    files: dict[str, str] = {}
    bundles: dict[str, list[BaseModel]] = defaultdict(list)
    for source_id, resource_spec in sorted(specs.items()):
        path = adapter.path_in(planned, source_id)
        if adapter.is_bundle_file(path):
            bundles[path].append(resource_spec)
        else:
            files[path] = serialize_yaml_model(resource_spec)
    for path, bundled in bundles.items():
        files[path] = adapter.dump_bundle(
            adapter.bundle_entry(resource_spec) for resource_spec in bundled
        )
    return files


def validate_workspace_dependencies(
    spec: WorkspaceSpec,
    *,
    paths: PathsByType | None = None,
) -> list[PullDiagnostic]:
    """Check cross-resource references in a workspace spec for dangling links.

    Flags workflows that reference unknown child workflow aliases or agent preset
    slugs, agent presets that reference missing skills or subagents, and cyclic
    subagent references. Returns one :class:`PullDiagnostic` per problem found.
    Diagnostics name each resource's path in ``paths``, or its planned
    readable path when ``paths`` does not have it.
    """
    diagnostics: list[PullDiagnostic] = []
    planned = plan_resource_paths(spec, fixed=paths)

    def workflow_path(source_id: str) -> str:
        return WORKFLOW_RESOURCE_ADAPTER.path_in(planned, source_id)

    def preset_path(source_id: str) -> str:
        return AGENT_PRESET_RESOURCE_ADAPTER.path_in(planned, source_id)

    workflow_source_ids_by_alias: dict[str, list[str]] = defaultdict(list)
    for source_id, workflow in sorted(spec.workflows.items()):
        if workflow.alias:
            workflow_source_ids_by_alias[workflow.alias].append(source_id)
    workflow_aliases = set(workflow_source_ids_by_alias)
    preset_specs_by_slug = {
        preset.slug: preset for preset in spec.agent_presets.values()
    }
    preset_slugs = set(preset_specs_by_slug)
    skill_specs_by_slug = {skill.slug: skill for skill in spec.skills.values()}
    skill_slugs = set(skill_specs_by_slug)

    for alias, source_ids in sorted(workflow_source_ids_by_alias.items()):
        if len(source_ids) <= 1:
            continue
        diagnostics.append(
            PullDiagnostic(
                workflow_path=workflow_path(source_ids[0]),
                workflow_title=None,
                error_type="dependency",
                message=f"Duplicate workflow alias {alias!r} declared by multiple workflows",
                details={"alias": alias, "workflow_source_ids": source_ids},
            )
        )

    diagnostics.extend(_validate_unique_import_identities(spec, planned))

    default_source_ids = sorted(
        source_id
        for source_id, preset in spec.agent_presets.items()
        if preset.is_workspace_default is True
    )
    if len(default_source_ids) > 1:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=preset_path(default_source_ids[0]),
                workflow_title=None,
                error_type="dependency",
                message="Only one agent preset can be the workspace default",
                details={"agent_preset_source_ids": default_source_ids},
            )
        )

    for source_id, workflow in sorted(spec.workflows.items()):
        references = workflow_references(workflow.definition)
        for alias in sorted(references.execute_aliases):
            if alias not in workflow_aliases:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=workflow_path(source_id),
                        workflow_title=workflow.definition.title,
                        error_type="dependency",
                        message=f"Workflow references missing child workflow alias {alias!r}",
                        details={"workflow_source_id": source_id, "alias": alias},
                    )
                )
        for preset_slug in sorted(references.preset_slugs):
            if preset_slug not in preset_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=workflow_path(source_id),
                        workflow_title=workflow.definition.title,
                        error_type="dependency",
                        message=f"Workflow references missing agent preset slug {preset_slug!r}",
                        details={
                            "workflow_source_id": source_id,
                            "preset_slug": preset_slug,
                        },
                    )
                )
        for preset_slug, _ in sorted(references.versioned_preset_slugs):
            if preset_slug not in preset_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=workflow_path(source_id),
                        workflow_title=workflow.definition.title,
                        error_type="dependency",
                        message=f"Workflow references missing agent preset slug {preset_slug!r}",
                        details={
                            "workflow_source_id": source_id,
                            "preset_slug": preset_slug,
                        },
                    )
                )
                continue

    preset_graph: dict[str, list[str]] = {}
    for source_id, preset in sorted(spec.agent_presets.items()):
        preset_graph[preset.slug] = [subagent.slug for subagent in preset.subagents]
        for skill in preset.skills:
            if skill.slug not in skill_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=preset_path(source_id),
                        workflow_title=preset.name,
                        error_type="dependency",
                        message=f"Agent preset references missing skill slug {skill.slug!r}",
                        details={"preset_slug": preset.slug, "skill_slug": skill.slug},
                    )
                )
        for subagent in preset.subagents:
            if subagent.slug not in preset_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=preset_path(source_id),
                        workflow_title=preset.name,
                        error_type="dependency",
                        message=f"Agent preset references missing subagent slug {subagent.slug!r}",
                        details={
                            "preset_slug": preset.slug,
                            "subagent_slug": subagent.slug,
                        },
                    )
                )

    if cycle := _find_cycle(preset_graph):
        diagnostics.append(
            PullDiagnostic(
                workflow_path="",
                workflow_title=None,
                error_type="dependency",
                message="Cyclic agent preset subagent reference detected: "
                + " -> ".join(cycle),
                details={"cycle": cycle},
            )
        )
    return diagnostics


def _validate_unique_import_identities(
    spec: WorkspaceSpec, paths: PathsByType
) -> list[PullDiagnostic]:
    """Reject non-workflow specs that would collide during adapter import."""
    diagnostics: list[PullDiagnostic] = []
    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        specs = adapter.specs(spec)
        source_ids_by_identity: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for source_id, resource_spec in sorted(specs.items()):
            identity = adapter.import_identity(resource_spec)
            if identity is None:
                continue
            source_ids_by_identity[identity].append(source_id)

        for identity, source_ids in sorted(source_ids_by_identity.items()):
            if len(source_ids) <= 1:
                continue
            first_spec = specs[source_ids[0]]
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=adapter.path_in(paths, source_ids[0]),
                    workflow_title=adapter.display_name(first_spec),
                    error_type="validation",
                    message=(
                        f"Duplicate {adapter.resource_type.value} "
                        f"{adapter.import_identity_noun} "
                        f"{_format_import_identity(identity)} declared by multiple "
                        "resources"
                    ),
                    details={
                        "resource_type": adapter.resource_type.value,
                        "identity_attrs": list(adapter.import_identity_attrs),
                        "identity": list(identity),
                        "source_ids": source_ids,
                    },
                )
            )
    return diagnostics


def _format_import_identity(identity: tuple[str, ...]) -> str:
    """Format a sync identity for validation diagnostics."""
    return "/".join(repr(part) for part in identity)


class WorkflowReferences(NamedTuple):
    """Cross-resource references extracted from a workflow definition."""

    execute_aliases: set[str]
    execute_ids: set[WorkflowUUID]
    preset_slugs: set[str]
    versioned_preset_slugs: set[VersionedSlug]


def _static_reference(value: object) -> str | None:
    """Return ``value`` when it is a literal, non-empty reference string.

    Templated values (``${{ ... }}``) are resolved at runtime and cannot be
    checked against the workspace at sync time, so they are not references.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if patterns.TEMPLATE_STRING.search(value):
        return None
    return value


def workflow_references(definition: DSLInput) -> WorkflowReferences:
    """Collect static child workflow and agent preset references in one pass."""
    execute_aliases: set[str] = set()
    execute_ids: set[WorkflowUUID] = set()
    preset_slugs: set[str] = set()
    versioned_preset_slugs: set[VersionedSlug] = set()
    for action in definition.actions:
        match action:
            case ActionStatement(
                action=PlatformAction.CHILD_WORKFLOW_EXECUTE, args=args
            ):
                # Mirror runtime resolution (see dsl/action.py): alias takes
                # precedence over id, so collect at most one child reference.
                if isinstance(raw_alias := args.get("workflow_alias"), str):
                    if (alias := _static_reference(raw_alias)) is not None:
                        execute_aliases.add(alias)
                elif (
                    workflow_id := _static_reference(args.get("workflow_id"))
                ) is not None:
                    try:
                        execute_ids.add(WorkflowUUID.new(workflow_id))
                    except ValueError:
                        pass
            case ActionStatement(
                action=PlatformAction.AI_PRESET_AGENT,
                args=args,
            ):
                preset_slug = args.get("preset")
                if not isinstance(preset_slug, str):
                    preset_slug = args.get("preset_slug")
                if (preset_slug := _static_reference(preset_slug)) is not None:
                    if isinstance(version := args.get("preset_version"), int):
                        versioned_preset_slugs.add(VersionedSlug(preset_slug, version))
                    else:
                        preset_slugs.add(preset_slug)
    return WorkflowReferences(
        execute_aliases,
        execute_ids,
        preset_slugs,
        versioned_preset_slugs,
    )


def _parse_yaml_resource[ModelT: BaseModel](
    path: str,
    content: str,
    *,
    default_source_id: str,
    model: type[ModelT],
    diagnostics: list[PullDiagnostic],
) -> ModelT | None:
    """Parse one YAML resource file, or record a diagnostic and return ``None``.

    Loads ``content``, backfills the ``id`` field from ``default_source_id``
    (the file's folder or file name) when absent, and validates it against
    ``model``. An empty file, YAML error, or validation error appends a
    :class:`PullDiagnostic` instead of raising.
    """
    yaml_data: dict[str, Any] | None = None
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or not raw:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=None,
                    error_type="parse",
                    message="Empty or invalid resource YAML file",
                    details={},
                )
            )
            return None
        yaml_data = raw
        if "id" not in raw:
            raw = {**raw, "id": default_source_id}
        return model.model_validate(raw)
    except yaml.YAMLError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=None,
                error_type="parse",
                message=f"YAML parsing error: {str(e)}",
                details={"yaml_error": str(e)},
            )
        )
    except ValidationError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=_resource_title(yaml_data),
                error_type="validation",
                message=f"Validation error: {str(e)}",
                details={
                    "validation_errors": serializable_validation_errors(e.errors())
                },
            )
        )
    except Exception as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=_resource_title(yaml_data),
                error_type="parse",
                message=f"Unexpected parsing error: {str(e)}",
                details={"error": str(e)},
            )
        )
    return None


def _resource_title(data: dict[str, Any] | None) -> str | None:
    """Return a resource's ``name`` or ``title`` for diagnostics, if present."""
    if not data:
        return None
    name = data.get("name")
    if isinstance(name, str):
        return name
    title = data.get("title")
    return title if isinstance(title, str) else None


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    """Return the first dependency cycle in ``graph`` as a node path, else ``[]``.

    The returned list repeats the entry node at both ends (e.g.
    ``["a", "b", "a"]``). Nodes and edges are visited in sorted order for
    deterministic results.

    Implemented as an iterative depth-first search to avoid recursion limits on
    deep dependency chains. ``on_stack`` maps each node on the active path to its
    stack depth, so a back edge yields the cycle in O(cycle length).
    """
    adjacency = {node: sorted(children) for node, children in graph.items()}
    visited: set[str] = set()
    on_stack: dict[str, int] = {}

    for root in sorted(adjacency):
        if root in visited:
            continue
        on_stack[root] = 0
        stack: list[tuple[str, Iterator[str]]] = [(root, iter(adjacency[root]))]
        while stack:
            node, children = stack[-1]
            for child in children:
                if child in on_stack:
                    return [frame[0] for frame in stack[on_stack[child] :]] + [child]
                if child not in visited:
                    on_stack[child] = len(stack)
                    stack.append((child, iter(adjacency.get(child, []))))
                    break
            else:
                stack.pop()
                del on_stack[node]
                visited.add(node)
    return []
