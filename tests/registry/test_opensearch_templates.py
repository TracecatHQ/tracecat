"""Catalog and request-contract tests for OpenSearch template actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tracecat_registry import RegistrySecret

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/opensearch"
)
AUTHORIZATION = (
    'Basic ${{ FN.to_base64(SECRETS.opensearch.OPENSEARCH_USERNAME + ":" + '
    "SECRETS.opensearch.OPENSEARCH_PASSWORD) }}"
)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    method: str
    endpoint_fragment: str

    @property
    def action(self) -> str:
        return f"tools.opensearch.{self.name}"


CATALOG = (
    CatalogEntry("list_indexes", "GET", "_cat/indices"),
    CatalogEntry("get_mapping", "GET", "_mapping"),
    CatalogEntry("search_events", "POST", "_search"),
    CatalogEntry("multi_search", "POST", "_msearch"),
    CatalogEntry("get_document", "GET", "_doc"),
    CatalogEntry("multi_get_documents", "POST", "_mget"),
    CatalogEntry("count_events", "POST", "_count"),
    CatalogEntry("ppl_query", "POST", "_plugins/_ppl"),
    CatalogEntry(
        "list_security_analytics_alerts",
        "GET",
        "_plugins/_security_analytics/alerts",
    ),
    CatalogEntry(
        "acknowledge_security_analytics_alerts",
        "POST",
        "_plugins/_security_analytics/detectors",
    ),
    CatalogEntry(
        "list_security_analytics_findings",
        "GET",
        "_plugins/_security_analytics/findings/_search",
    ),
    CatalogEntry(
        "search_detectors",
        "POST",
        "_plugins/_security_analytics/detectors/_search",
    ),
    CatalogEntry(
        "search_detection_rules",
        "POST",
        "_plugins/_security_analytics/rules/_search",
    ),
    CatalogEntry(
        "list_monitor_alerts",
        "GET",
        "_plugins/_alerting/monitors/alerts",
    ),
    CatalogEntry(
        "acknowledge_monitor_alerts",
        "POST",
        "_plugins/_alerting/monitors",
    ),
)


@pytest.fixture(scope="module")
def templates() -> dict[str, tuple[TemplateAction, Path]]:
    loaded: dict[str, tuple[TemplateAction, Path]] = {}
    for path in sorted(TEMPLATE_ROOT.rglob("*.yml")):
        template = TemplateAction.from_yaml(path)
        loaded[template.definition.action] = (template, path)
    return loaded


def http_step(template: TemplateAction):
    return next(
        step for step in template.definition.steps if step.action == "core.http_request"
    )


def execute_script(template: TemplateAction, ref: str):
    step = next(step for step in template.definition.steps if step.ref == ref)
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)  # noqa: S102
    return namespace["main"]


def get_template(
    templates: dict[str, tuple[TemplateAction, Path]], name: str
) -> TemplateAction:
    return templates[f"tools.opensearch.{name}"][0]


def test_catalog_is_exact(templates: dict[str, tuple[TemplateAction, Path]]) -> None:
    expected = {entry.action for entry in CATALOG}
    assert len(CATALOG) == 15
    assert len(expected) == 15
    assert set(templates) == expected


@pytest.mark.parametrize("entry", CATALOG, ids=lambda entry: entry.action)
def test_metadata_method_and_endpoint(
    entry: CatalogEntry,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, path = templates[entry.action]
    definition = template.definition

    assert definition.namespace == "tools.opensearch"
    assert definition.name == entry.name
    assert definition.display_group == "OpenSearch"
    assert definition.doc_url is not None
    assert definition.doc_url.startswith("https://docs.opensearch.org/")
    assert http_step(template).args["method"] == entry.method
    assert entry.endpoint_fragment in path.read_text()


def test_common_authentication_and_connection_contract(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    for action, (template, _) in templates.items():
        definition = template.definition
        assert definition.secrets is not None
        assert len(definition.secrets) == 1
        secret = definition.secrets[0]
        assert isinstance(secret, RegistrySecret), action
        assert secret.name == "opensearch", action
        assert secret.keys == [
            "OPENSEARCH_USERNAME",
            "OPENSEARCH_PASSWORD",
        ], action

        assert definition.expects["base_url"].type == "str | None", action
        assert definition.expects["base_url"].default is None, action
        assert definition.expects["verify_ssl"].type == "bool", action
        assert definition.expects["verify_ssl"].default is True, action

        request = http_step(template)
        assert request.args["verify_ssl"] == "${{ inputs.verify_ssl }}", action
        assert request.args["headers"]["Authorization"] == AUTHORIZATION, action
        assert "inputs.base_url || VARS.opensearch.base_url" in request.args["url"]
        assert definition.returns == "${{ steps.request.result.data }}", action


@pytest.mark.parametrize(
    ("name", "suffix"),
    (
        ("search_events", "_search"),
        ("multi_search", "_msearch"),
        ("multi_get_documents", "_mget"),
        ("count_events", "_count"),
    ),
)
def test_optional_index_paths(
    name: str,
    suffix: str,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    build_path = execute_script(get_template(templates, name), "build_path")
    assert build_path(None) == f"/{suffix}"
    assert build_path("logs-*/events") == f"/logs-*%2Fevents/{suffix}"


def test_index_discovery_and_mapping_paths(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    list_indexes = get_template(templates, "list_indexes")
    build_path = execute_script(list_indexes, "build_path")
    build_params = execute_script(list_indexes, "build_params")
    assert build_path(None) == "/_cat/indices"
    assert build_path("logs-*/events") == "/_cat/indices/logs-*%2Fevents"
    assert build_params(None) == {"format": "json"}
    assert build_params({"health": "yellow", "format": "yaml"}) == {
        "health": "yellow",
        "format": "json",
    }

    get_mapping = get_template(templates, "get_mapping")
    mapping_path = execute_script(get_mapping, "build_path")
    assert mapping_path("logs-*/events") == "/logs-*%2Fevents/_mapping"


def test_document_identifiers_are_url_encoded(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    _, path = templates["tools.opensearch.get_document"]
    source = path.read_text()
    assert "FN.url_encode(inputs.index)" in source
    assert "FN.url_encode(inputs.document_id)" in source


def test_search_payload_has_bounded_default(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template = get_template(templates, "search_events")
    build_payload = execute_script(template, "build_payload")
    assert build_payload({"query": {"match_all": {}}}, 100) == {
        "query": {"match_all": {}},
        "size": 100,
    }
    explicit = {"query": {"match_all": {}}, "size": 0}
    assert build_payload(explicit, 100) is explicit
    assert http_step(template).args["params"] == "${{ inputs.params }}"


def test_multi_search_uses_native_ndjson(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template = get_template(templates, "multi_search")
    normalize = execute_script(template, "normalize_ndjson")
    ndjson = '{}\n{"query":{"match_all":{}}}'
    assert normalize(ndjson) == f"{ndjson}\n"
    assert normalize(f"{ndjson}\n") == f"{ndjson}\n"

    request = http_step(template)
    assert request.args["content"] == "${{ steps.normalize_ndjson.result }}"
    assert "payload" not in request.args
    assert request.args["headers"]["Content-Type"] == "application/x-ndjson"


@pytest.mark.parametrize(
    "name",
    (
        "multi_get_documents",
        "count_events",
        "search_detectors",
        "search_detection_rules",
    ),
)
def test_api_native_payloads_pass_through(
    name: str,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template = get_template(templates, name)
    assert template.definition.expects["payload"].type == "dict[str, Any]"
    assert http_step(template).args["payload"] == "${{ inputs.payload }}"


def test_ppl_query_contract(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template = get_template(templates, "ppl_query")
    assert template.definition.expects["format"].default == "jdbc"
    request = http_step(template)
    assert request.args["params"] == {"format": "${{ inputs.format }}"}
    assert request.args["payload"] == {"query": "${{ inputs.query }}"}


def test_detection_rule_partition_is_explicit(
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template = get_template(templates, "search_detection_rules")
    assert template.definition.expects["pre_packaged"].default is True
    assert http_step(template).args["params"] == {
        "pre_packaged": "${{ inputs.pre_packaged }}"
    }


@pytest.mark.parametrize(
    ("name", "identifier"),
    (
        ("acknowledge_security_analytics_alerts", "detector_id"),
        ("acknowledge_monitor_alerts", "monitor_id"),
    ),
)
def test_acknowledgement_contracts(
    name: str,
    identifier: str,
    templates: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, path = templates[f"tools.opensearch.{name}"]
    request = http_step(template)
    assert request.args["payload"] == {"alerts": "${{ inputs.alert_ids }}"}
    assert f"FN.url_encode(inputs.{identifier})" in path.read_text()
