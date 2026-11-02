"""Tests for parsing and matching the managed resources package include file."""

from __future__ import annotations

import pytest

from tracecat.managed_resources.include import (
    PackageIncludeError,
    PackageResourceKey,
    matched_resources,
    parse_package_include,
    render_package_include,
    unmatched_patterns,
)
from tracecat.workspace_sync.enums import SyncResourceType

WORKFLOW_A = PackageResourceKey(SyncResourceType.WORKFLOW, "wf-a")
WORKFLOW_B = PackageResourceKey(SyncResourceType.WORKFLOW, "wf-b")
SKILL = PackageResourceKey(SyncResourceType.SKILL, "triage-notes")
TABLE = PackageResourceKey(SyncResourceType.TABLE, "iocs")

PATHS = {
    WORKFLOW_A: ["workflows/wf-a/definition.yml"],
    WORKFLOW_B: ["workflows/wf-b/definition.yml"],
    SKILL: ["skills/triage-notes/skill.yml", "skills/triage-notes/files/SKILL.md"],
    TABLE: ["tables/iocs/table.yml"],
}


def test_allowlist_selects_only_matching_resources() -> None:
    include = parse_package_include("workflows/wf-a/**\nskills/triage-notes/**\n")

    assert matched_resources(include, PATHS) == {WORKFLOW_A, SKILL}


def test_negation_removes_a_resource_matched_by_a_broader_pattern() -> None:
    include = parse_package_include("workflows/**\n!workflows/wf-b/**\n")

    assert matched_resources(include, PATHS) == {WORKFLOW_A}


def test_companion_file_match_selects_its_resource() -> None:
    include = parse_package_include("skills/*/files/SKILL.md\n")

    assert matched_resources(include, PATHS) == {SKILL}


def test_empty_file_selects_nothing() -> None:
    include = parse_package_include("")

    assert include.patterns == ()
    assert matched_resources(include, PATHS) == set()


def test_comments_and_blank_lines_are_ignored() -> None:
    include = parse_package_include("# header\n\n  tables/iocs/table.yml  \n")

    assert include.patterns == ("tables/iocs/table.yml",)
    assert matched_resources(include, PATHS) == {TABLE}


def test_malformed_pattern_is_an_error() -> None:
    with pytest.raises(PackageIncludeError):
        parse_package_include("!\n")


def test_pattern_matching_nothing_is_reported() -> None:
    include = parse_package_include(
        "workflows/wf-a/**\nworkflows/gone/**\n!workflows/other/**\n"
    )

    all_paths = [path for paths in PATHS.values() for path in paths]
    # Negations are not reported even when they remove nothing.
    assert unmatched_patterns(include, all_paths) == ["workflows/gone/**"]


def test_render_keeps_order_dedupes_and_round_trips() -> None:
    content = render_package_include(
        ["workflows/wf-b/**", "workflows/wf-a/**", "workflows/wf-a/**", "  "]
    )

    assert content.startswith("# ")
    assert parse_package_include(content).patterns == (
        "workflows/wf-b/**",
        "workflows/wf-a/**",
    )


def test_render_keeps_a_negation_after_the_pattern_it_narrows() -> None:
    include = parse_package_include(
        render_package_include(["workflows/**", "!workflows/wf-b/**"])
    )

    assert matched_resources(include, PATHS) == {WORKFLOW_A}


def test_render_rejects_a_malformed_pattern() -> None:
    with pytest.raises(PackageIncludeError):
        render_package_include(["!"])
