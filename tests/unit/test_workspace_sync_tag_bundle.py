"""Case tags share one bundle file in Git sync repositories.

Pushes write every tag into ``case_tags/_all.yml``. Repositories written
before bundling hold one ``case_tags/<name>.yml`` file per tag. These tests
pin both directions: old files still parse, and the next push moves each tag
it writes into the bundle without dropping tags it does not write.
"""

from __future__ import annotations

import pytest
import yaml

from tracecat.managed_resources.include import (
    PackageResourceKey,
    matched_resources,
    parse_package_include,
)
from tracecat.workspace_sync.adapters import CASE_TAG_RESOURCE_ADAPTER
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.exceptions import (
    PushBundleUnreadableError,
    PushNameConflictError,
)
from tracecat.workspace_sync.layout import (
    ResourcePathIndex,
    partial_push_delete_roots,
    plan_push_paths,
    plan_resource_paths,
)
from tracecat.workspace_sync.package import file_names_by_resource
from tracecat.workspace_sync.resources import (
    is_primary_resource_path,
    locate_resources,
    merge_base_bundles,
    parse_workspace_spec_files,
    serialize_workspace_spec_files,
)
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    CaseTagResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceSpec,
)
from tracecat.workspace_sync.serialization import (
    canonical_json_text,
    serialize_yaml_model,
)

CASE_TAG = SyncResourceType.CASE_TAG
BUNDLE = "case_tags/_all.yml"


def _tag(source_id: str, name: str, color: str | None = None) -> CaseTagResourceSpec:
    return CaseTagResourceSpec(id=source_id, name=name, color=color)


def _tags(*tags: CaseTagResourceSpec) -> WorkspaceSpec:
    return WorkspaceSpec(case_tags={tag.id: tag for tag in tags})


def _serialize(spec: WorkspaceSpec, paths=None) -> dict[str, str]:
    return serialize_workspace_spec_files(
        manifest=WorkspaceManifest(),
        spec=spec,
        manifest_filename=MANIFEST_FILENAME,
        manifest_serializer=canonical_json_text,
        paths=paths,
    )


def _parse(files: dict[str, str]):
    return parse_workspace_spec_files(
        {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()), **files},
        manifest=WorkspaceManifest(),
    )


def _bundle(*entries: dict[str, object]) -> str:
    return yaml.safe_dump({"version": 1, "tags": list(entries)}, sort_keys=False)


def test_push_writes_every_tag_into_one_bundle_sorted_by_id() -> None:
    spec = _tags(_tag("urgent", "Urgent", "#ff0000"), _tag("phishing", "Phishing"))

    files = _serialize(spec)

    assert set(files) == {MANIFEST_FILENAME, BUNDLE}
    assert yaml.safe_load(files[BUNDLE]) == {
        "version": 1,
        "tags": [
            {"id": "phishing", "name": "Phishing"},
            {"id": "urgent", "name": "Urgent", "color": "#ff0000"},
        ],
    }


def test_bundle_round_trips_through_parse() -> None:
    spec = _tags(_tag("urgent", "Urgent", "#ff0000"), _tag("phishing", "Phishing"))

    parsed = _parse(_serialize(spec))

    assert parsed.diagnostics == []
    assert parsed.spec.case_tags == spec.case_tags
    assert parsed.paths[CASE_TAG] == {"phishing": BUNDLE, "urgent": BUNDLE}


def test_legacy_per_tag_files_still_parse() -> None:
    files = {
        "case_tags/Urgent.yml": serialize_yaml_model(_tag("urgent", "Urgent")),
        # Very old files had no ``id``; the file name is the id.
        "case_tags/phishing.yml": "name: Phishing\n",
    }

    parsed = _parse(files)

    assert parsed.diagnostics == []
    assert parsed.spec.case_tags == {
        "phishing": _tag("phishing", "Phishing"),
        "urgent": _tag("urgent", "Urgent"),
    }
    assert parsed.paths[CASE_TAG] == {
        "phishing": "case_tags/phishing.yml",
        "urgent": "case_tags/Urgent.yml",
    }


def test_mixed_repository_reads_both_and_the_bundle_wins_a_duplicate() -> None:
    # A branch pushed before bundling merged into one pushed after it.
    files = {
        BUNDLE: _bundle({"id": "urgent", "name": "Urgent"}),
        "case_tags/Urgent.yml": serialize_yaml_model(_tag("urgent", "Old name")),
        "case_tags/phishing.yml": serialize_yaml_model(_tag("phishing", "Phishing")),
    }

    parsed = _parse(files)

    assert parsed.spec.case_tags == {
        "phishing": _tag("phishing", "Phishing"),
        "urgent": _tag("urgent", "Urgent"),
    }
    [diagnostic] = parsed.diagnostics
    assert diagnostic.details["paths"] == [BUNDLE, "case_tags/Urgent.yml"]


def test_one_bad_entry_does_not_hide_the_others() -> None:
    files = {BUNDLE: _bundle({"id": "urgent", "name": "Urgent"}, {"id": "no-name"})}

    parsed = _parse(files)

    assert list(parsed.spec.case_tags) == ["urgent"]
    [diagnostic] = parsed.diagnostics
    assert diagnostic.workflow_path == BUNDLE
    assert diagnostic.error_type == "validation"
    assert diagnostic.details["entry_index"] == 1


@pytest.mark.parametrize(
    "content",
    [
        "tags: [",
        "- id: urgent\n",
        "version: 2\ntags: []\n",
        "version: 1\ntags: {}\n",
        # A versioned file without ``tags`` is not an empty bundle.
        "version: 1\nitems: []\n",
    ],
)
def test_unreadable_bundle_is_a_diagnostic(content: str) -> None:
    parsed = _parse({BUNDLE: content})

    assert parsed.spec.case_tags == {}
    [diagnostic] = parsed.diagnostics
    assert diagnostic.workflow_path == BUNDLE
    assert diagnostic.error_type == "parse"


def test_bundle_file_is_never_read_as_one_tag() -> None:
    roots = WorkspaceManifest().resources

    assert CASE_TAG_RESOURCE_ADAPTER.path_key_from_path(BUNDLE, roots) is None
    assert CASE_TAG_RESOURCE_ADAPTER.path_key_from_path("case_tags/x.yml", roots)
    assert is_primary_resource_path(BUNDLE)


def test_custom_manifest_root_reads_its_bundle() -> None:
    manifest = WorkspaceManifest.model_validate(
        {"resources": {"case_tags": "config/tags/"}}
    )

    parsed = parse_workspace_spec_files(
        {
            MANIFEST_FILENAME: canonical_json_text(manifest),
            "config/tags/_all.yml": _bundle({"id": "urgent", "name": "Urgent"}),
        },
        manifest=manifest,
    )

    assert parsed.diagnostics == []
    assert list(parsed.spec.case_tags) == ["urgent"]


def test_push_moves_legacy_tag_files_into_the_bundle() -> None:
    spec = _tags(_tag("urgent", "Urgent"))
    # The mapping still remembers the per-tag file the last push wrote.
    paths = plan_push_paths(
        spec,
        mapped={CASE_TAG: {"urgent": "case_tags/Urgent.yml"}},
        owns_repository=False,
    )
    base = {"case_tags/Urgent.yml": serialize_yaml_model(_tag("urgent", "Urgent"))}

    assert paths[CASE_TAG] == {"urgent": BUNDLE}
    assert partial_push_delete_roots(paths, locate_resources(base)) == [
        "case_tags/Urgent.yml"
    ]


def test_pushed_bundle_is_never_a_path_conflict() -> None:
    # The branch bundle holds a tag this push does not write. Per-tag files
    # would have been a conflict; the bundle keeps it instead.
    paths = plan_resource_paths(_tags(_tag("urgent", "Urgent")))
    base = {BUNDLE: _bundle({"id": "theirs", "name": "Theirs"})}

    assert partial_push_delete_roots(paths, locate_resources(base)) == []


def test_locate_reads_every_id_in_the_bundle() -> None:
    base = {BUNDLE: _bundle({"id": "a", "name": "A"}, {"id": "b", "name": "B"})}

    assert locate_resources(base) == {CASE_TAG: {"a": [BUNDLE], "b": [BUNDLE]}}


def test_partial_push_keeps_tags_it_does_not_write() -> None:
    pushed = _serialize(_tags(_tag("urgent", "Urgent v2")))
    base = {
        BUNDLE: _bundle(
            {"id": "urgent", "name": "Urgent"},
            {"id": "theirs", "name": "Theirs", "color": "#000000"},
        )
    }

    merged = merge_base_bundles(pushed, base, owned_roots=[], base_paths=frozenset())

    assert yaml.safe_load(merged[BUNDLE])["tags"] == [
        {"id": "theirs", "name": "Theirs", "color": "#000000"},
        {"id": "urgent", "name": "Urgent v2"},
    ]


def test_push_that_owns_the_folder_replaces_the_bundle() -> None:
    # A full push deletes every tag it does not write.
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))
    base = {BUNDLE: _bundle({"id": "deleted-locally", "name": "Gone"})}

    merged = merge_base_bundles(
        pushed, base, owned_roots=["case_tags"], base_paths=frozenset()
    )

    assert merged == pushed


def test_partial_push_refuses_a_name_another_branch_tag_holds() -> None:
    # A teammate pushed "Urgent" under another id. Merging would leave two
    # tags named "Urgent", which the next pull rejects.
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))
    base = {BUNDLE: _bundle({"id": "theirs", "name": "Urgent"})}

    with pytest.raises(PushNameConflictError) as exc_info:
        merge_base_bundles(pushed, base, owned_roots=[], base_paths=frozenset())

    # The shared path alone would not say which tag clashed.
    assert exc_info.value.clashes == [f"{BUNDLE} (Urgent)"]


def test_partial_push_refuses_a_name_a_leftover_tag_file_holds() -> None:
    # The branch still has a file from before bundling for another tag with
    # the pushed tag's name. Separate files used to conflict on this path.
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))
    base = {"case_tags/Urgent.yml": serialize_yaml_model(_tag("foo", "Urgent"))}

    with pytest.raises(PushNameConflictError) as exc_info:
        merge_base_bundles(pushed, base, owned_roots=[], base_paths=frozenset())

    assert exc_info.value.clashes == ["case_tags/Urgent.yml (Urgent)"]


def test_stale_leftover_copy_of_a_bundled_tag_is_not_a_conflict() -> None:
    # A pull reads tag x from the bundle, so its old name in a leftover file
    # does not clash with a pushed tag taking that name.
    pushed = _serialize(_tags(_tag("y", "Old")))
    base = {
        BUNDLE: _bundle({"id": "x", "name": "New"}),
        "case_tags/Old.yml": serialize_yaml_model(_tag("x", "Old")),
    }

    merged = merge_base_bundles(pushed, base, owned_roots=[], base_paths=frozenset())

    assert [tag["id"] for tag in yaml.safe_load(merged[BUNDLE])["tags"]] == ["x", "y"]


def test_partial_push_moves_its_own_leftover_file_without_a_conflict() -> None:
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))
    base = {"case_tags/Urgent.yml": serialize_yaml_model(_tag("urgent", "Urgent"))}

    assert (
        merge_base_bundles(pushed, base, owned_roots=[], base_paths=frozenset())
        == pushed
    )


def test_partial_push_refuses_a_bundle_it_could_not_read_as_text() -> None:
    # Transports list non-UTF-8 files but leave out their contents.
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))

    with pytest.raises(PushBundleUnreadableError):
        merge_base_bundles(pushed, {}, owned_roots=[], base_paths={BUNDLE})


def test_partial_push_refuses_to_overwrite_an_unreadable_bundle() -> None:
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))

    for base in ("tags: [", "version: 1\nitems: [{id: theirs}]\n"):
        with pytest.raises(PushBundleUnreadableError) as exc_info:
            merge_base_bundles(
                pushed, {BUNDLE: base}, owned_roots=[], base_paths=frozenset()
            )

        assert exc_info.value.path == BUNDLE


def test_pull_of_a_legacy_repository_serializes_at_the_paths_it_read() -> None:
    # Pull previews diff local state at the incoming paths, so a repository
    # that still has per-tag files diffs file by file.
    spec = _tags(_tag("urgent", "Urgent"), _tag("phishing", "Phishing"))
    fixed = {
        CASE_TAG: {"urgent": "case_tags/Urgent.yml", "phishing": BUNDLE},
    }

    files = _serialize(spec, paths=plan_resource_paths(spec, fixed=fixed))

    assert yaml.safe_load(files["case_tags/Urgent.yml"])["id"] == "urgent"
    assert yaml.safe_load(files[BUNDLE])["tags"] == [
        {"id": "phishing", "name": "Phishing"}
    ]


def test_bundle_entries_split_for_per_tag_diffs() -> None:
    content = _bundle({"id": "a", "name": "A"}, {"name": "no id"})

    assert CASE_TAG_RESOURCE_ADAPTER.entry_texts(content) == {"a": "id: a\nname: A\n"}
    assert CASE_TAG_RESOURCE_ADAPTER.entry_texts("tags: [") == {}


def test_bundle_path_has_no_single_owner() -> None:
    paths = plan_resource_paths(_tags(_tag("a", "A"), _tag("b", "B")))

    assert ResourcePathIndex(paths).owner(BUNDLE) is None


def test_readable_alias_never_takes_another_tags_id_path() -> None:
    # "Critical" was mapped as "urgent" before a rename; a new tag named
    # "urgent" got the id "urgent-2". The picker's pattern for "Critical"
    # must not also select the new tag.
    spec = _tags(_tag("urgent", "Critical"), _tag("urgent-2", "urgent"))
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=spec,
        files=_serialize(spec),
        paths=plan_resource_paths(spec),
    )

    matched = matched_resources(
        parse_package_include("case_tags/urgent.yml"),
        file_names_by_resource(projection),
    )

    assert matched == {PackageResourceKey(CASE_TAG, "urgent")}


@pytest.mark.parametrize(
    ("pattern", "selected"),
    [
        # Both names map to "Alert-High"; the per-tag planner gave it to "a"
        # and suffixed "b", so each old path still names one tag.
        ("case_tags/Alert-High.yml", {"a"}),
        ("case_tags/Alert-High-b.yml", {"b"}),
    ],
)
def test_readable_aliases_follow_the_per_tag_collision_rules(
    pattern: str, selected: set[str]
) -> None:
    spec = _tags(_tag("a", "Alert/High"), _tag("b", "Alert High"))
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=spec,
        files=_serialize(spec),
        paths=plan_resource_paths(spec),
    )

    matched = matched_resources(
        parse_package_include(pattern), file_names_by_resource(projection)
    )

    assert matched == {PackageResourceKey(CASE_TAG, tag) for tag in selected}


@pytest.mark.parametrize(
    ("pattern", "selected"),
    [
        # What the picker writes for one tag.
        ("case_tags/urgent.yml", {"urgent"}),
        # Hand-written before bundling, by readable name.
        ("case_tags/Urgent.yml", {"urgent"}),
        ("case_tags/**", {"urgent", "phishing"}),
        ("case_tags/**\n!case_tags/Urgent.yml", {"phishing"}),
    ],
)
def test_include_patterns_still_select_bundled_tags(
    pattern: str, selected: set[str]
) -> None:
    spec = _tags(_tag("urgent", "Urgent"), _tag("phishing", "Phishing"))
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=spec,
        files=_serialize(spec),
        paths=plan_resource_paths(spec),
    )

    matched = matched_resources(
        parse_package_include(pattern), file_names_by_resource(projection)
    )

    assert matched == {PackageResourceKey(CASE_TAG, tag) for tag in selected}
