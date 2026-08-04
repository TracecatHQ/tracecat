"""Contract tests for the agent-oriented MISP enrichment actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tracecat_registry import RegistrySecret

from tracecat.dsl.schemas import TemplateExecutionContext
from tracecat.expressions.eval import eval_templated_object
from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/misp"
)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    method: str
    endpoint: str

    @property
    def action(self) -> str:
        return f"tools.misp.{self.name}"


CATALOG = (
    CatalogEntry("search_events", "POST", "/events/restSearch"),
    CatalogEntry("search_attributes", "POST", "/attributes/restSearch"),
    CatalogEntry("search_objects", "POST", "/objects/restSearch"),
    CatalogEntry("search_event_index", "POST", "/events/index"),
    CatalogEntry("get_attribute", "GET", "/attributes/view/"),
    CatalogEntry("get_object", "GET", "/objects/view/"),
    CatalogEntry("search_tags", "POST", "/tags/search"),
    CatalogEntry("list_taxonomies", "GET", "/taxonomies/index"),
    CatalogEntry("get_taxonomy", "GET", "/taxonomies/view/"),
    CatalogEntry("search_galaxies", "POST", "/galaxies"),
    CatalogEntry("get_galaxy", "GET", "/galaxies/view/"),
    CatalogEntry(
        "search_galaxy_clusters",
        "POST",
        "/galaxy_clusters/index/",
    ),
)

APPROVED_ACTION_NAMES = frozenset(
    "search_events search_attributes search_objects search_event_index "
    "get_event get_attribute get_object search_tags list_taxonomies get_taxonomy "
    "search_galaxies get_galaxy search_galaxy_clusters search_feeds".split()
)


@pytest.fixture(scope="module")
def templates() -> dict[str, tuple[TemplateAction, Path]]:
    loaded: dict[str, tuple[TemplateAction, Path]] = {}
    for path in sorted(TEMPLATE_ROOT.rglob("*.yml")):
        template = TemplateAction.from_yaml(path)
        action = template.definition.action
        if action in loaded:
            pytest.fail(f"Duplicate action in {loaded[action][1]} and {path}")
        loaded[action] = (template, path)
    return loaded


def http_step(template: TemplateAction):
    return next(
        step for step in template.definition.steps if step.action == "core.http_request"
    )


def execute_script(template: TemplateAction, ref: str, **inputs: Any) -> Any:
    step = next(step for step in template.definition.steps if step.ref == ref)
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)  # noqa: S102
    return namespace["main"](**inputs)


def test_catalog_matches_official_mcp_surface(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    expected = {f"tools.misp.{name}" for name in APPROVED_ACTION_NAMES}
    assert len(expected) == 14
    assert set(templates) == expected


@pytest.mark.parametrize("entry", CATALOG, ids=lambda entry: entry.action)
def test_action_method_and_endpoint(
    entry: CatalogEntry,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = templates[entry.action]
    definition = template.definition
    request = http_step(template)

    assert definition.namespace == "tools.misp"
    assert definition.name == entry.name
    assert definition.display_group == "MISP"
    assert definition.doc_url is not None
    assert definition.doc_url.startswith("https://www.misp-project.org/")
    assert request.args["method"] == entry.method
    assert entry.endpoint in request.args["url"]


def test_common_connection_contract(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    for action, (template, _) in templates.items():
        definition = template.definition
        assert definition.secrets is not None
        assert len(definition.secrets) == 1
        secret = definition.secrets[0]
        assert isinstance(secret, RegistrySecret), action
        assert secret.name == "misp", action
        assert secret.keys == ["MISP_API_KEY"], action

        assert definition.expects["base_url"].type == "str | None", action
        assert definition.expects["base_url"].default is None, action
        assert definition.expects["verify_ssl"].type == "bool", action
        assert definition.expects["verify_ssl"].default is True, action
        assert "payload" not in definition.expects, action
        assert "params" not in definition.expects, action

        request = http_step(template)
        assert request.args["verify_ssl"] == "${{ inputs.verify_ssl }}", action
        assert request.args["headers"]["Authorization"] == (
            "${{ SECRETS.misp.MISP_API_KEY }}"
        ), action
        assert "inputs.base_url || VARS.misp.base_url" in request.args["url"], action
        assert definition.returns == "${{ steps.request.result.data }}", action


def test_catalog_contains_only_read_only_enrichment_paths(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    forbidden_path_fragments = (
        "/add",
        "/delete",
        "/edit",
        "/publish",
        "/unpublish",
        "/enable",
        "/disable",
        "/update",
        "/servers",
        "/users",
        "/auth",
        "/admin",
        "/sharing_groups",
        "/sightings",
    )
    for action, (template, path) in templates.items():
        source = path.read_text()
        assert not any(fragment in source for fragment in forbidden_path_fragments), (
            action
        )
        method = http_step(template).args["method"]
        assert method in {"GET", "POST", "${{ steps.build_request.result.method }}"}


@pytest.mark.parametrize(
    ("action", "identifier", "path"),
    (
        ("tools.misp.get_event", "event_id", "/events/view/42"),
        ("tools.misp.get_attribute", "attribute_id", "/attributes/view/42"),
        ("tools.misp.get_object", "object_id", "/objects/view/42"),
        ("tools.misp.get_taxonomy", "taxonomy_id", "/taxonomies/view/42"),
        ("tools.misp.get_galaxy", "galaxy_id", "/galaxies/view/42"),
        (
            "tools.misp.search_galaxy_clusters",
            "galaxy_id",
            "/galaxy_clusters/index/42",
        ),
    ),
)
def test_numeric_identifiers_are_encoded_as_strings(
    action: str,
    identifier: str,
    path: str,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = templates[action]
    inputs = {
        identifier: 42,
        "base_url": "https://misp.example.com",
        "include_correlations": False,
        "include_sightings": False,
    }
    context = TemplateExecutionContext(inputs=inputs, steps={})

    url = eval_templated_object(http_step(template).args["url"], operand=context)

    assert url == f"https://misp.example.com{path}"


def test_search_feeds_matches_official_list_or_search_behavior(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = templates["tools.misp.search_feeds"]

    assert execute_script(template, "build_request", value=None) == {
        "method": "GET",
        "path": "/feeds/index",
        "payload": None,
    }
    assert execute_script(template, "build_request", value="example.test") == {
        "method": "POST",
        "path": "/feeds/searchCaches",
        "payload": {"value": "example.test"},
    }


def test_get_event_matches_official_direct_or_enriched_behavior(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, path = templates["tools.misp.get_event"]

    assert execute_script(
        template,
        "build_request",
        event_id=42,
        include_correlations=False,
        include_sightings=False,
    ) == {"method": "GET", "payload": None}
    assert execute_script(
        template,
        "build_request",
        event_id=42,
        include_correlations=True,
        include_sightings=False,
    ) == {
        "method": "POST",
        "payload": {
            "eventid": 42,
            "includeCorrelations": True,
            "includeSightings": False,
            "limit": 1,
        },
    }
    source = path.read_text()
    assert "/events/view/" in source
    assert "/events/restSearch" in source


@pytest.mark.parametrize(
    "action",
    ("tools.misp.get_event", "tools.misp.search_feeds"),
)
def test_dual_method_actions_leave_content_type_to_http_client(
    action: str,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = templates[action]

    assert "Content-Type" not in http_step(template).args["headers"]


@pytest.mark.parametrize(
    ("sort", "desc", "expected"),
    (
        ("date", None, None),
        ("date", False, "asc"),
        ("date", True, "desc"),
        (None, True, None),
    ),
)
def test_event_index_preserves_optional_sort_direction(
    sort: str | None,
    desc: bool | None,
    expected: str | None,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = templates["tools.misp.search_event_index"]
    context = TemplateExecutionContext(
        inputs={"sort": sort, "desc": desc},
        steps={},
    )
    direction = http_step(template).args["payload"]["direction"]

    assert eval_templated_object(direction, operand=context) == expected


def test_search_defaults_are_bounded_and_agent_friendly(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    expected_limits = {
        "tools.misp.search_events": 25,
        "tools.misp.search_attributes": 50,
        "tools.misp.search_objects": 25,
        "tools.misp.search_event_index": 25,
    }
    for action, limit in expected_limits.items():
        definition = templates[action][0].definition
        assert definition.expects["limit"].default == limit
        assert definition.expects["page"].default == 1

    assert (
        templates["tools.misp.search_galaxy_clusters"][0]
        .definition.expects["context"]
        .default
        is None
    )
