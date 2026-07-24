"""Regression tests for the `tools.google_scc` templates.

The templates wrap the generic `tools.google_api` actions, deriving the
discovery-client resource path (`organizations.sources.findings`, etc.) from
the scope or finding name via inline Python steps. These tests pin the exact
request wiring — service, version, resource, and method — without spinning up
the executor or calling Google.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATES_DIR = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/google_scc"
)

# SCC v2 always returns location-qualified finding names. These mirror the shape
# of a real `list_findings` response, which is what gets fed to the write-back
# actions.
ORG_FINDING = "organizations/123456789/sources/5678/locations/global/findings/abc123"
PROJECT_FINDING = (
    "projects/example-project/sources/5678/locations/global/findings/abc123"
)
# The non-location shape is still accepted (v1-style / other callers).
ORG_FINDING_NO_LOCATION = "organizations/123456789/sources/5678/findings/abc123"


def load_template(filename: str) -> TemplateAction:
    return TemplateAction.from_yaml(TEMPLATES_DIR / filename)


def get_step(template: TemplateAction, ref: str):
    return next(s for s in template.definition.steps if s.ref == ref)


def get_script(template: TemplateAction, ref: str):
    namespace: dict[str, Any] = {}
    exec(get_step(template, ref).args["script"], namespace)  # noqa: S102
    return namespace["main"]


@pytest.mark.parametrize(
    "filename",
    [
        "list_findings.yml",
        "set_finding_state.yml",
        "mute_finding.yml",
        "list_sources.yml",
    ],
)
def test_template_parses_with_expected_namespace(filename: str) -> None:
    template = load_template(filename)
    assert template.definition.namespace == "tools.google_scc"
    assert template.definition.name == filename.removesuffix(".yml")


@pytest.mark.parametrize(
    ("filename", "call_ref", "expected_action", "expected_method"),
    [
        (
            "list_findings.yml",
            "list_findings",
            "tools.google_api.call_paginated_api",
            "list",
        ),
        ("set_finding_state.yml", "set_state", "tools.google_api.call_api", "setState"),
        ("mute_finding.yml", "set_mute", "tools.google_api.call_api", "setMute"),
        (
            "list_sources.yml",
            "list_sources",
            "tools.google_api.call_paginated_api",
            "list",
        ),
    ],
)
def test_request_wiring(
    filename: str, call_ref: str, expected_action: str, expected_method: str
) -> None:
    template = load_template(filename)
    step = get_step(template, call_ref)
    assert step.action == expected_action
    assert step.args["service_name"] == "securitycenter"
    assert step.args["version"] == "v2"
    assert step.args["method_name"] == expected_method
    # v2 is not bundled in the client's static discovery docs, so every SCC
    # call must fetch the discovery document at runtime.
    assert step.args["static_discovery"] is False


class TestListFindingsBuildRequest:
    @pytest.fixture(scope="class")
    def build_request(self):
        return get_script(load_template("list_findings.yml"), "build_request")

    def test_organization_scope_defaults(self, build_request) -> None:
        result = build_request("organizations/123456789", "-", None, None, 100)
        assert result == {
            "resource": "organizations.sources.findings",
            "params": {
                "parent": "organizations/123456789/sources/-",
                "pageSize": 100,
            },
        }

    def test_project_scope_with_filter_and_order(self, build_request) -> None:
        result = build_request(
            "projects/example-project",
            "-",
            'state="ACTIVE"',
            "event_time desc",
            50,
        )
        assert result["resource"] == "projects.sources.findings"
        assert result["params"] == {
            "parent": "projects/example-project/sources/-",
            "pageSize": 50,
            "filter": 'state="ACTIVE"',
            "orderBy": "event_time desc",
        }

    def test_folder_scope(self, build_request) -> None:
        result = build_request("folders/987", "-", None, None, 100)
        assert result["resource"] == "folders.sources.findings"
        assert result["params"]["parent"] == "folders/987/sources/-"

    @pytest.mark.parametrize(
        "bad_scope",
        [
            "",  # empty
            "organizations",  # no id
            "organizations/",  # empty id
            "projects",  # no id
            "billingAccounts/123",  # not a valid SCC scope type
            "__class__/x",  # would otherwise be traversed via getattr
        ],
    )
    def test_rejects_invalid_scope(self, build_request, bad_scope: str) -> None:
        # The scope type becomes part of the discovery-client resource path, so
        # it must be validated before it is resolved via getattr.
        with pytest.raises(ValueError, match="scope"):
            build_request(bad_scope, "-", None, None, 100)


class TestListFindingsFlatten:
    @pytest.fixture(scope="class")
    def flatten(self):
        return get_script(load_template("list_findings.yml"), "flatten")

    def test_flattens_results_across_pages(self, flatten) -> None:
        pages = [
            {
                "listFindingsResults": [
                    {"finding": {"name": ORG_FINDING}, "resource": {"name": "vm-1"}}
                ],
                "nextPageToken": "t",
            },
            {
                "listFindingsResults": [
                    {"finding": {"name": PROJECT_FINDING}, "resource": {"name": "vm-2"}}
                ]
            },
        ]
        result = flatten(pages)
        assert [r["finding"]["name"] for r in result["findings"]] == [
            ORG_FINDING,
            PROJECT_FINDING,
        ]
        # Last page carries no nextPageToken -> the result set is complete.
        assert result["truncated"] is False
        assert result["next_page_token"] is None

    def test_empty_pages_return_empty_list(self, flatten) -> None:
        # Pages with no findings omit `listFindingsResults` entirely.
        result = flatten([{"totalSize": 0}])
        assert result["findings"] == []
        assert result["truncated"] is False
        assert result["total_size"] == 0

    def test_no_pages_at_all(self, flatten) -> None:
        result = flatten([])
        assert result["findings"] == []
        assert result["truncated"] is False

    def test_truncated_result_is_flagged(self, flatten) -> None:
        # `max_pages` stops the fetch early, so the final page still carries a
        # nextPageToken. A silently truncated list would let a triage workflow
        # believe it had seen every finding.
        pages = [
            {
                "listFindingsResults": [
                    {"finding": {"name": ORG_FINDING}, "resource": {"name": "vm-1"}}
                ],
                "nextPageToken": "more-findings-exist",
                "totalSize": 20000,
            }
        ]
        result = flatten(pages)
        assert result["truncated"] is True
        assert result["next_page_token"] == "more-findings-exist"
        assert result["total_size"] == 20000
        assert len(result["findings"]) == 1


@pytest.mark.parametrize("filename", ["set_finding_state.yml", "mute_finding.yml"])
class TestFindingResourceDerivation:
    def test_location_qualified_organization_finding(self, filename: str) -> None:
        # This is the shape SCC v2 actually returns from list_findings. The
        # non-location resource rejects it via its `name` pattern, so it must
        # resolve to the `.locations.` variant.
        build_resource = get_script(load_template(filename), "build_resource")
        assert build_resource(ORG_FINDING) == "organizations.sources.locations.findings"

    def test_location_qualified_project_finding(self, filename: str) -> None:
        build_resource = get_script(load_template(filename), "build_resource")
        assert build_resource(PROJECT_FINDING) == "projects.sources.locations.findings"

    def test_non_location_finding_uses_plain_resource(self, filename: str) -> None:
        build_resource = get_script(load_template(filename), "build_resource")
        assert (
            build_resource(ORG_FINDING_NO_LOCATION) == "organizations.sources.findings"
        )

    @pytest.mark.parametrize(
        "bad_name",
        [
            "",
            "organizations/123",  # not a finding name
            "organizations/123/sources/5678",  # missing /findings/
            "billingAccounts/123/sources/1/findings/abc",  # invalid scope type
            "__class__/sources/1/findings/abc",  # would be traversed via getattr
        ],
    )
    def test_rejects_invalid_finding_name(self, filename: str, bad_name: str) -> None:
        # The scope type becomes part of the discovery-client resource path, so
        # it must be validated before it is resolved via getattr.
        build_resource = get_script(load_template(filename), "build_resource")
        with pytest.raises(ValueError, match="finding_name"):
            build_resource(bad_name)


def test_set_state_passes_name_and_state_body() -> None:
    template = load_template("set_finding_state.yml")
    params = get_step(template, "set_state").args["params"]
    assert params["name"] == "${{ inputs.finding_name }}"
    assert params["body"] == {"state": "${{ inputs.state }}"}


def test_mute_passes_name_and_mute_body() -> None:
    template = load_template("mute_finding.yml")
    params = get_step(template, "set_mute").args["params"]
    assert params["name"] == "${{ inputs.finding_name }}"
    assert params["body"] == {"mute": "${{ inputs.mute }}"}


def test_list_sources_build_request() -> None:
    build_request = get_script(load_template("list_sources.yml"), "build_request")
    assert build_request("organizations/123456789", 100) == {
        "resource": "organizations.sources",
        "params": {"parent": "organizations/123456789", "pageSize": 100},
    }


def test_list_sources_flatten() -> None:
    flatten = get_script(load_template("list_sources.yml"), "flatten")
    pages = [
        {"sources": [{"name": "organizations/123456789/sources/1"}]},
        {"sources": [{"name": "organizations/123456789/sources/2"}]},
        {},
    ]
    result = flatten(pages)
    assert [s["name"] for s in result["sources"]] == [
        "organizations/123456789/sources/1",
        "organizations/123456789/sources/2",
    ]
    assert result["truncated"] is False


def test_list_findings_expects_contract() -> None:
    expects = load_template("list_findings.yml").definition.expects
    assert expects["source"].default == "-"
    assert expects["page_size"].default == 100
    assert expects["filter"].type == "str | None"
    # Bounded by default: an unbounded fetch materializes every page in memory
    # and returns them as one action result, which can OOM the executor on a
    # real org scope. Callers opt into a full fetch explicitly (null).
    assert expects["max_pages"].default == 10
