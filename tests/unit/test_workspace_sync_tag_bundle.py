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
from tracecat.workspace_sync.exceptions import PushBundleUnreadableError
from tracecat.workspace_sync.layout import (
    ResourcePathIndex,
    partial_push_delete_roots,
    plan_push_paths,
    plan_resource_paths,
)
from tracecat.workspace_sync.package import file_names_by_resource
from tracecat.workspace_sync.resources import (
    bundle_entry_texts,
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
    ["tags: [", "- id: urgent\n", "version: 2\ntags: []\n", "version: 1\ntags: {}\n"],
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

    merged = merge_base_bundles(pushed, base, owned_roots=[])

    assert yaml.safe_load(merged[BUNDLE])["tags"] == [
        {"id": "theirs", "name": "Theirs", "color": "#000000"},
        {"id": "urgent", "name": "Urgent v2"},
    ]


def test_push_that_owns_the_folder_replaces_the_bundle() -> None:
    # A full push deletes every tag it does not write.
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))
    base = {BUNDLE: _bundle({"id": "deleted-locally", "name": "Gone"})}

    merged = merge_base_bundles(pushed, base, owned_roots=["case_tags"])

    assert merged == pushed


def test_partial_push_refuses_to_overwrite_an_unreadable_bundle() -> None:
    pushed = _serialize(_tags(_tag("urgent", "Urgent")))

    with pytest.raises(PushBundleUnreadableError) as exc_info:
        merge_base_bundles(pushed, {BUNDLE: "tags: ["}, owned_roots=[])

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

    assert bundle_entry_texts(CASE_TAG_RESOURCE_ADAPTER, content) == {
        "a": "id: a\nname: A\n"
    }
    assert bundle_entry_texts(CASE_TAG_RESOURCE_ADAPTER, "tags: [") == {}


def test_bundle_path_has_no_single_owner() -> None:
    paths = plan_resource_paths(_tags(_tag("a", "A"), _tag("b", "B")))

    assert ResourcePathIndex(paths).owner(BUNDLE) is None


@pytest.mark.parametrize(
    "pattern",
    [
        # What the picker writes for one tag.
        "case_tags/urgent.yml",
        "case_tags/**",
    ],
)
def test_include_patterns_still_select_bundled_tags(pattern: str) -> None:
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

    urgent = PackageResourceKey(CASE_TAG, "urgent")
    phishing = PackageResourceKey(CASE_TAG, "phishing")
    assert matched == (
        {urgent} if pattern.endswith("urgent.yml") else {urgent, phishing}
    )
