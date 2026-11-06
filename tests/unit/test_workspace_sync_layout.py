"""Readable repository paths for workspace sync.

Folders are named after each resource's current name, and identity is the
``id`` inside each file. These tests pin the path rules: collisions, renames,
legacy id-named folders, and package include patterns.
"""

from __future__ import annotations

import pytest
import yaml

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.managed_resources.include import (
    PACKAGE_INCLUDE_FILENAME,
    PackageResourceKey,
    matched_resources,
    parse_package_include,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.exceptions import PushPathConflictError
from tracecat.workspace_sync.layout import (
    ResourcePathIndex,
    partial_push_delete_roots,
    plan_push_paths,
    plan_resource_paths,
    without_resources,
)
from tracecat.workspace_sync.package import (
    file_names_by_resource,
    include_pattern_for,
)
from tracecat.workspace_sync.resources import (
    is_primary_resource_path,
    locate_resources,
    parse_workspace_spec_files,
    serialize_workspace_spec_files,
)
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    SkillResourceSpec,
    TableResourceSpec,
    VariableResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceSpec,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.transport import BaseWorkspaceSyncTransport
from tracecat.workspace_sync.workflow import serialize_workflow_spec

WORKFLOW = SyncResourceType.WORKFLOW


def _workflow(source_id: str, title: str) -> WorkflowResourceSpec:
    return WorkflowResourceSpec(
        id=source_id,
        alias=source_id,
        definition=DSLInput(
            title=title,
            description="Layout test workflow",
            entrypoint=DSLEntrypoint(ref="start", expects={}),
            actions=[
                ActionStatement(
                    ref="start",
                    action="core.transform.passthrough",
                    args={"value": "x"},
                )
            ],
        ),
    )


def _workflows(*workflows: WorkflowResourceSpec) -> WorkspaceSpec:
    return WorkspaceSpec(workflows={workflow.id: workflow for workflow in workflows})


def _serialize(spec: WorkspaceSpec) -> dict[str, str]:
    return serialize_workspace_spec_files(
        manifest=WorkspaceManifest(),
        spec=spec,
        manifest_filename=MANIFEST_FILENAME,
        manifest_serializer=canonical_json_text,
    )


def test_workflow_folder_is_named_after_its_title() -> None:
    spec = _workflows(_workflow("wf_4f2a", "Triage alerts"))

    paths = plan_resource_paths(spec)

    assert paths[WORKFLOW] == {"wf_4f2a": "workflows/Triage-alerts/definition.yml"}


def test_unsafe_or_empty_titles_fall_back_to_the_source_id() -> None:
    spec = _workflows(
        _workflow("wf_slash", "a/b\\c"),
        _workflow("wf_unicode", "トリアージ"),
    )

    paths = plan_resource_paths(spec)

    assert paths[WORKFLOW] == {
        # Path separators never create nested folders.
        "wf_slash": "workflows/a-b-c/definition.yml",
        "wf_unicode": "workflows/wf_unicode/definition.yml",
    }


def test_same_name_collision_appends_the_source_id() -> None:
    # Folders that differ only by case would collide on macOS and Windows.
    spec = _workflows(
        _workflow("wf_b", "Triage"),
        _workflow("wf_a", "triage"),
    )

    paths = plan_resource_paths(spec)

    assert paths[WORKFLOW] == {
        "wf_a": "workflows/triage/definition.yml",
        "wf_b": "workflows/Triage-wf_b/definition.yml",
    }


def test_existing_resource_keeps_its_folder_when_a_same_named_one_appears() -> None:
    # wf_b already lives at "Triage/". wf_a sorts first but is new, so it must
    # not push wf_b aside.
    spec = _workflows(_workflow("wf_a", "Triage"), _workflow("wf_b", "Triage"))

    paths = plan_resource_paths(
        spec, previous={WORKFLOW: {"wf_b": "workflows/Triage/definition.yml"}}
    )

    assert paths[WORKFLOW] == {
        "wf_a": "workflows/Triage-wf_a/definition.yml",
        "wf_b": "workflows/Triage/definition.yml",
    }


def test_reserved_paths_are_never_reused() -> None:
    spec = _workflows(_workflow("wf_new", "Triage"))

    paths = plan_resource_paths(
        spec, reserved={WORKFLOW: ["workflows/triage/definition.yml"]}
    )

    assert paths[WORKFLOW] == {"wf_new": "workflows/Triage-wf_new/definition.yml"}


def _tree(*workflows: tuple[str, WorkflowResourceSpec]) -> dict[str, str]:
    """Build a base tree holding each workflow at the given folder."""
    return {
        f"workflows/{folder}/definition.yml": serialize_workflow_spec(workflow)
        for folder, workflow in workflows
    }


def test_rename_moves_the_folder_and_keeps_the_id() -> None:
    before = _workflows(_workflow("wf_4f2a", "Triage alerts"))
    after = _workflows(_workflow("wf_4f2a", "Triage incidents"))
    mapped = plan_resource_paths(before)
    base = _tree(("Triage-alerts", before.workflows["wf_4f2a"]))

    paths = plan_push_paths(after, mapped=mapped, owns_repository=False)
    files = _serialize(after)

    new_path = "workflows/Triage-incidents/definition.yml"
    assert paths[WORKFLOW] == {"wf_4f2a": new_path}
    assert yaml.safe_load(files[new_path])["id"] == "wf_4f2a"
    assert partial_push_delete_roots(paths, locate_resources(base)) == [
        "workflows/Triage-alerts"
    ]


def test_cleanup_finds_the_legacy_id_folder_from_the_tree() -> None:
    workflow = _workflow("wf_4f2a", "Triage")
    paths = plan_resource_paths(_workflows(workflow))

    base = locate_resources(_tree(("wf_4f2a", workflow)))

    assert partial_push_delete_roots(paths, base) == ["workflows/wf_4f2a"]


def test_cleanup_deletes_every_old_copy_on_the_branch() -> None:
    # Two renames pushed as two pull requests from the same base can leave
    # the workflow in two old folders. The next push removes both, whatever
    # the mapping last recorded.
    workflow = _workflow("wf_a", "Triage v3")
    paths = plan_resource_paths(_workflows(workflow))
    base = _tree(("Triage", workflow), ("Triage-v2", workflow))

    assert partial_push_delete_roots(paths, locate_resources(base)) == [
        "workflows/Triage",
        "workflows/Triage-v2",
    ]


def test_partial_push_refuses_to_overwrite_another_resource() -> None:
    # A teammate pushed wf_x into workflows/Triage. This workspace never
    # pulled it, so its own "Triage" workflow must not write over it.
    ours = _workflow("wf_b", "Triage")
    theirs = _workflow("wf_x", "Triage")
    paths = plan_resource_paths(_workflows(ours))
    base = locate_resources(_tree(("Triage", theirs)))

    with pytest.raises(PushPathConflictError) as exc_info:
        partial_push_delete_roots(paths, base)

    assert exc_info.value.locations == ["workflows/Triage"]


def test_moving_into_a_folder_another_pushed_resource_leaves_is_fine() -> None:
    # wf_a and wf_b swap names in one push: each writes where the other was.
    a, b = _workflow("wf_a", "Enrich"), _workflow("wf_b", "Triage")
    paths = plan_resource_paths(_workflows(a, b))
    base = locate_resources(_tree(("Triage", a), ("Enrich", b)))

    assert partial_push_delete_roots(paths, base) == [
        "workflows/Enrich",
        "workflows/Triage",
    ]


def test_own_previous_path_wins_over_an_out_of_date_mapping() -> None:
    # A deleted workflow's mapping still claims "Foo/". Two resources cannot
    # share a folder, so that claim is out of date: wf_a keeps "Foo/" on a
    # partial push instead of moving to "Foo-wf_a/".
    spec = _workflows(_workflow("wf_a", "Foo"))

    paths = plan_push_paths(
        spec,
        mapped={
            WORKFLOW: {
                "wf_a": "workflows/Foo/definition.yml",
                "wf_deleted": "workflows/Foo/definition.yml",
            }
        },
        owns_repository=False,
    )

    assert paths[WORKFLOW] == {"wf_a": "workflows/Foo/definition.yml"}


def test_locate_follows_the_parser_identity_rules() -> None:
    legacy = yaml.safe_load(serialize_workflow_spec(_workflow("unused", "Two")))
    del legacy["id"]
    files = {
        **_tree(("Triage", _workflow("wf_a", "Triage"))),
        # No id: the folder name is the id.
        "workflows/wf_folder/definition.yml": yaml.safe_dump(legacy),
        "README.md": "not a resource",
    }

    expected = {
        WORKFLOW: {
            "wf_a": ["workflows/Triage/definition.yml"],
            "wf_folder": ["workflows/wf_folder/definition.yml"],
        }
    }
    assert locate_resources(files) == expected
    # The full parser gives the same ids for the same files.
    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())
    assert sorted(parsed.spec.workflows) == sorted(expected[WORKFLOW])


def test_push_base_reads_only_primary_files() -> None:
    assert is_primary_resource_path("workflows/Triage/definition.yml")
    assert is_primary_resource_path("skills/enrich/skill.yml")
    assert not is_primary_resource_path("skills/enrich/files/SKILL.md")
    assert not is_primary_resource_path("README.md")


def test_readable_name_never_takes_another_resources_id_path() -> None:
    # Table "Assets" was renamed "Hosts" but keeps source id "Assets". A new
    # table named "Assets" must not land on tables/Assets/, or the include
    # pattern tables/Assets/** would select both tables.
    spec = WorkspaceSpec(
        tables={
            "Assets": TableResourceSpec(id="Assets", name="Hosts"),
            "Assets-2": TableResourceSpec(id="Assets-2", name="Assets"),
        }
    )
    paths = plan_resource_paths(spec)
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(), spec=spec, files=_serialize(spec), paths=paths
    )

    assert paths[SyncResourceType.TABLE] == {
        "Assets": "tables/Hosts/table.yml",
        "Assets-2": "tables/Assets-Assets-2/table.yml",
    }
    matched = matched_resources(
        parse_package_include("tables/Assets/**"), file_names_by_resource(projection)
    )
    assert matched == {PackageResourceKey(SyncResourceType.TABLE, "Assets")}


def test_package_selection_keeps_its_collision_suffixed_path() -> None:
    # The full projection placed wf_b at Triage-wf_b, and the include pattern
    # matched that path. Planned without wf_a, wf_b would move to Triage/ and
    # stop matching; fixing it to the full projection's paths keeps it put.
    a, b = _workflow("wf_a", "Triage"), _workflow("wf_b", "Triage")
    full = plan_resource_paths(_workflows(a, b))
    assert full[WORKFLOW]["wf_b"] == "workflows/Triage-wf_b/definition.yml"

    selection = plan_push_paths(
        _workflows(b), mapped={}, owns_repository=True, fixed=full
    )

    assert selection[WORKFLOW] == {"wf_b": "workflows/Triage-wf_b/definition.yml"}


def test_fixed_paths_of_absent_resources_stay_reserved() -> None:
    # The pull preview places local state at the incoming paths. A local-only
    # workflow must not land on an incoming workflow's path.
    spec = _workflows(_workflow("wf_local", "Triage"))

    paths = plan_resource_paths(
        spec, fixed={WORKFLOW: {"wf_incoming": "workflows/Triage/definition.yml"}}
    )

    assert paths[WORKFLOW] == {"wf_local": "workflows/Triage-wf_local/definition.yml"}


def test_ids_differing_only_by_case_plan_the_same_in_any_order() -> None:
    first = WorkspaceSpec(
        tables={
            "Assets": TableResourceSpec(id="Assets", name="Assets"),
            "assets": TableResourceSpec(id="assets", name="assets"),
        }
    )
    second = WorkspaceSpec(tables=dict(reversed(list(first.tables.items()))))

    assert plan_resource_paths(first) == plan_resource_paths(second)


def test_dropping_resources_drops_their_paths() -> None:
    locations = {
        WORKFLOW: {"wf_keep": "workflows/Keep", "wf_managed": "workflows/Managed"}
    }

    kept = without_resources(locations, {(WORKFLOW, "wf_managed")})

    assert kept == {WORKFLOW: {"wf_keep": "workflows/Keep"}}


def test_environment_scoped_paths_follow_environment_and_name() -> None:
    spec = WorkspaceSpec(
        variables={
            "default/api_token": VariableResourceSpec(
                id="default/api_token", name="vendor token", environment="prod"
            )
        }
    )

    paths = plan_resource_paths(spec)

    assert paths[SyncResourceType.VARIABLE] == {
        "default/api_token": "variables/prod/vendor-token.yml"
    }


def test_parse_reads_identity_from_the_file_not_the_folder() -> None:
    spec = _workflows(_workflow("wf_4f2a", "Triage alerts"))
    files = _serialize(spec)

    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())

    assert parsed.diagnostics == []
    assert list(parsed.spec.workflows) == ["wf_4f2a"]
    assert parsed.paths[WORKFLOW] == {
        "wf_4f2a": "workflows/Triage-alerts/definition.yml"
    }


def test_parse_accepts_legacy_id_folders_with_and_without_an_id_field() -> None:
    with_id = _workflow("wf_legacy", "Legacy one")
    without_id = yaml.safe_load(serialize_workflow_spec(_workflow("unused", "Two")))
    del without_id["id"]
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        "workflows/wf_legacy/definition.yml": serialize_workflow_spec(with_id),
        "workflows/wf_folder/definition.yml": yaml.safe_dump(without_id),
    }

    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())

    assert parsed.diagnostics == []
    # Without an id, the folder name is the id, as it always was.
    assert sorted(parsed.spec.workflows) == ["wf_folder", "wf_legacy"]


def test_parse_rejects_two_files_with_the_same_id() -> None:
    # A rename pushed by an older client, or a copied folder, can leave the
    # same workflow in two folders. The first path wins; the copy is reported.
    content = serialize_workflow_spec(_workflow("wf_4f2a", "Triage"))
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        "workflows/Triage/definition.yml": content,
        "workflows/wf_4f2a/definition.yml": content,
    }

    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())

    assert list(parsed.spec.workflows) == ["wf_4f2a"]
    assert parsed.paths[WORKFLOW] == {"wf_4f2a": "workflows/Triage/definition.yml"}
    [diagnostic] = parsed.diagnostics
    assert diagnostic.workflow_path == "workflows/wf_4f2a/definition.yml"
    assert diagnostic.details["paths"] == [
        "workflows/Triage/definition.yml",
        "workflows/wf_4f2a/definition.yml",
    ]


def test_skill_files_travel_with_a_renamed_skill_folder() -> None:
    skill = SkillResourceSpec(
        id="skill_source_id",
        slug="enrich-ip",
        name="Enrich IP",
        file_contents={"SKILL.md": "# Enrich\n"},
    )
    spec = WorkspaceSpec(skills={"skill_source_id": skill})

    files = _serialize(spec)

    assert "skills/enrich-ip/skill.yml" in files
    assert files["skills/enrich-ip/files/SKILL.md"] == "# Enrich\n"
    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())
    assert parsed.spec.skills["skill_source_id"].file_contents == {
        "SKILL.md": "# Enrich\n"
    }


def test_round_trip_through_files_preserves_the_spec() -> None:
    spec = _workflows(
        _workflow("wf_a", "Triage"),
        _workflow("wf_b", "Triage"),
        _workflow("wf_c", "Enrich"),
    )

    parsed = parse_workspace_spec_files(_serialize(spec), manifest=WorkspaceManifest())

    assert parsed.diagnostics == []
    assert parsed.spec == spec


def test_path_index_labels_old_locations_with_the_real_source_id() -> None:
    index = ResourcePathIndex(
        {WORKFLOW: {"wf_4f2a": "workflows/Triage/definition.yml"}},
        {WORKFLOW: {"wf_4f2a": ["workflows/wf_4f2a/definition.yml"]}},
    )

    owner = index.owner("workflows/Triage/definition.yml")
    assert owner is not None and owner.source_id == "wf_4f2a"
    # The old id folder a push deletes is labelled with the same resource.
    stale = index.owner("workflows/wf_4f2a/definition.yml")
    assert stale is not None and stale.source_id == "wf_4f2a"
    # Paths no resource claims are not guessed at.
    assert index.owner("workflows/unknown/definition.yml") is None
    assert index.owner("README.md") is None


@pytest.mark.parametrize(
    "pattern",
    [
        # What the picker writes: survives renames.
        "workflows/wf_4f2a/**",
        # Hand-written readable patterns also work.
        "workflows/Triage-incidents/**",
    ],
)
def test_include_patterns_match_readable_and_id_paths(pattern: str) -> None:
    spec = _workflows(_workflow("wf_4f2a", "Triage incidents"))
    paths = plan_resource_paths(spec)
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=spec,
        files=_serialize(spec),
        paths=paths,
    )
    key = PackageResourceKey(WORKFLOW, "wf_4f2a")

    matched = matched_resources(
        parse_package_include(pattern), file_names_by_resource(projection)
    )

    assert matched == {key}
    assert include_pattern_for(key) == "workflows/wf_4f2a/**"


@pytest.mark.anyio
async def test_filtered_snapshot_read_lists_everything_but_reads_primary_files() -> (
    None
):
    blob_paths = [
        MANIFEST_FILENAME,
        PACKAGE_INCLUDE_FILENAME,
        "README.md",
        "workflows/Triage/definition.yml",
        "skills/enrich/skill.yml",
        "skills/enrich/files/SKILL.md",
    ]
    fetched: list[str] = []

    async def fetch_text(path: str) -> str | None:
        fetched.append(path)
        return "{}" if path == MANIFEST_FILENAME else "content"

    files = await BaseWorkspaceSyncTransport._select_snapshot_files(
        blob_paths=blob_paths,
        fetch_text=fetch_text,
        resource_filter=is_primary_resource_path,
    )

    assert sorted(files) == sorted(
        [
            MANIFEST_FILENAME,
            PACKAGE_INCLUDE_FILENAME,
            "skills/enrich/skill.yml",
            "workflows/Triage/definition.yml",
        ]
    )
    assert "skills/enrich/files/SKILL.md" not in fetched
