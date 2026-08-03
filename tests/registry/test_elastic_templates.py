"""Catalog and contract tests for security-focused Elastic actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tracecat.expressions.functions import url_encode
from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_ROOT = Path("packages/tracecat-registry/tracecat_registry/templates/tools")


@dataclass(frozen=True)
class CatalogEntry:
    namespace: str
    name: str
    method: str
    endpoint_path: str
    display_group: str
    doc_url: str

    @property
    def action(self) -> str:
        return f"{self.namespace}.{self.name}"


def elasticsearch_entry(
    name: str, method: str, path: str, operation: str
) -> CatalogEntry:
    suffix = f"operation/operation-{operation}"
    return CatalogEntry(
        namespace="tools.elasticsearch",
        name=name,
        method=method,
        endpoint_path=path,
        display_group="Elasticsearch",
        doc_url=f"https://www.elastic.co/docs/api/doc/elasticsearch/{suffix}",
    )


def security_entry(
    name: str,
    method: str,
    path: str,
    operation: str,
    *,
    version: str | None = None,
) -> CatalogEntry:
    version_path = f"{version}/" if version else ""
    return CatalogEntry(
        namespace="tools.elastic_security",
        name=name,
        method=method,
        endpoint_path=path,
        display_group="Elastic Security",
        doc_url=(
            "https://www.elastic.co/docs/api/doc/kibana/"
            f"{version_path}operation/operation-{operation}"
        ),
    )


CATALOG = (
    # Threat hunting and event retrieval (7)
    elasticsearch_entry("list_indexes", "GET", "/_cat/indices/{index}", "cat-indices"),
    elasticsearch_entry("search_events", "POST", "/{index}/_search", "search"),
    elasticsearch_entry("get_document", "GET", "/{index}/_doc/{id}", "get"),
    elasticsearch_entry(
        "get_mapping", "GET", "/{index}/_mapping", "indices-get-mapping"
    ),
    elasticsearch_entry("esql", "POST", "/_query", "esql-query"),
    elasticsearch_entry("eql", "POST", "/{index}/_eql/search", "eql-search"),
    # Alerts and detection engineering (12)
    security_entry(
        "list_detection_signals",
        "POST",
        "/api/detection_engine/signals/search",
        "searchalerts",
        version="v8",
    ),
    security_entry(
        "search_detection_alerts",
        "POST",
        "/api/detection_engine/signals/search",
        "searchalerts",
    ),
    security_entry(
        "set_detection_alert_status",
        "POST",
        "/api/detection_engine/signals/status",
        "setalertsstatus",
    ),
    security_entry(
        "update_detection_alert_tags",
        "POST",
        "/api/detection_engine/signals/tags",
        "setalerttags",
    ),
    security_entry(
        "assign_detection_alert_users",
        "POST",
        "/api/detection_engine/signals/assignees",
        "setalertassignees",
    ),
    security_entry(
        "list_detection_rules", "GET", "/api/detection_engine/rules/_find", "findrules"
    ),
    security_entry(
        "create_detection_rule", "POST", "/api/detection_engine/rules", "createrule"
    ),
    security_entry(
        "patch_detection_rule", "PATCH", "/api/detection_engine/rules", "patchrule"
    ),
    security_entry(
        "bulk_action_detection_rules",
        "POST",
        "/api/detection_engine/rules/_bulk_action",
        "performrulesbulkaction",
    ),
    security_entry(
        "import_detection_rules",
        "POST",
        "/api/detection_engine/rules/_import",
        "importrules",
    ),
    security_entry(
        "export_detection_rules",
        "POST",
        "/api/detection_engine/rules/_export",
        "exportrules",
    ),
    security_entry(
        "preview_detection_rule",
        "POST",
        "/api/detection_engine/rules/preview",
        "rulepreview",
    ),
    # AI-assisted and live investigation (3)
    security_entry(
        "search_attack_discoveries",
        "GET",
        "/api/attack_discovery/_find",
        "attackdiscoveryfind",
    ),
    security_entry(
        "search_entities",
        "GET",
        "/api/security/entity_store/entities",
        "get-security-entity-store-entities",
    ),
    security_entry(
        "run_osquery_live_query",
        "POST",
        "/api/osquery/live_queries",
        "osquerycreatelivequery",
    ),
    # Exception-list containers and their conditions (5)
    security_entry(
        "create_exception_list", "POST", "/api/exception_lists", "createexceptionlist"
    ),
    security_entry(
        "list_exception_lists",
        "GET",
        "/api/exception_lists/_find",
        "findexceptionlists",
    ),
    security_entry(
        "create_exception_list_item",
        "POST",
        "/api/exception_lists/items",
        "createexceptionlistitem",
    ),
    security_entry(
        "list_exception_list_items",
        "GET",
        "/api/exception_lists/items/_find",
        "findexceptionlistitems",
    ),
    security_entry(
        "delete_exception_list_item",
        "DELETE",
        "/api/exception_lists/items",
        "deleteexceptionlistitem",
    ),
    # Endpoint response (20)
    security_entry(
        "list_endpoints", "GET", "/api/endpoint/metadata", "getendpointmetadatalist"
    ),
    security_entry(
        "get_endpoint", "GET", "/api/endpoint/metadata/{id}", "getendpointmetadata"
    ),
    security_entry(
        "list_response_actions", "GET", "/api/endpoint/action", "endpointgetactionslist"
    ),
    security_entry(
        "get_response_action",
        "GET",
        "/api/endpoint/action/{action_id}",
        "endpointgetactionsdetails",
    ),
    security_entry(
        "cancel_response_action", "POST", "/api/endpoint/action/cancel", "cancelaction"
    ),
    security_entry(
        "isolate_endpoint",
        "POST",
        "/api/endpoint/action/isolate",
        "endpointisolateaction",
    ),
    security_entry(
        "release_endpoint",
        "POST",
        "/api/endpoint/action/unisolate",
        "endpointunisolateaction",
    ),
    security_entry(
        "run_endpoint_command",
        "POST",
        "/api/endpoint/action/execute",
        "endpointexecuteaction",
    ),
    security_entry(
        "get_endpoint_processes",
        "POST",
        "/api/endpoint/action/running_procs",
        "endpointgetprocessesaction",
    ),
    security_entry(
        "terminate_endpoint_process",
        "POST",
        "/api/endpoint/action/kill_process",
        "endpointkillprocessaction",
    ),
    security_entry(
        "suspend_endpoint_process",
        "POST",
        "/api/endpoint/action/suspend_process",
        "endpointsuspendprocessaction",
    ),
    security_entry(
        "scan_endpoint_path", "POST", "/api/endpoint/action/scan", "endpointscanaction"
    ),
    security_entry(
        "get_endpoint_file",
        "POST",
        "/api/endpoint/action/get_file",
        "endpointgetfileaction",
    ),
    security_entry(
        "get_response_file_info",
        "GET",
        "/api/endpoint/action/{action_id}/file/{file_id}",
        "endpointfileinfo",
    ),
    security_entry(
        "download_response_file",
        "GET",
        "/api/endpoint/action/{action_id}/file/{file_id}/download",
        "endpointfiledownload",
    ),
    security_entry(
        "upload_endpoint_file",
        "POST",
        "/api/endpoint/action/upload",
        "endpointuploadaction",
    ),
    security_entry(
        "generate_endpoint_memory_dump",
        "POST",
        "/api/endpoint/action/memory_dump",
        "endpointgeneratememorydump",
    ),
    security_entry(
        "run_endpoint_script",
        "POST",
        "/api/endpoint/action/run_script",
        "runscriptaction",
    ),
    security_entry(
        "list_endpoint_scripts",
        "GET",
        "/api/endpoint/scripts_library",
        "endpointscriptlibrarylistscripts",
    ),
    security_entry(
        "create_endpoint_script",
        "POST",
        "/api/endpoint/scripts_library",
        "endpointscriptlibrarycreatescript",
    ),
)


def load_catalog() -> dict[str, tuple[TemplateAction, Path]]:
    loaded: dict[str, tuple[TemplateAction, Path]] = {}
    for integration in ("elasticsearch", "elastic_security"):
        for path in sorted((TEMPLATE_ROOT / integration).rglob("*.yml")):
            template = TemplateAction.from_yaml(path)
            loaded[template.definition.action] = (template, path)
    return loaded


@pytest.fixture(scope="module")
def loaded_catalog() -> dict[str, tuple[TemplateAction, Path]]:
    return load_catalog()


def http_step(template: TemplateAction):
    return next(
        step for step in template.definition.steps if step.action == "core.http_request"
    )


def execute_script(template: TemplateAction, ref: str):
    step = next(step for step in template.definition.steps if step.ref == ref)
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)  # noqa: S102
    return namespace["main"]


def test_catalog_is_exact_and_loads_by_fully_qualified_name(
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    expected = {entry.action for entry in CATALOG}
    assert len(CATALOG) == 46
    assert len(expected) == 46
    assert sum(entry.namespace == "tools.elasticsearch" for entry in CATALOG) == 6
    assert sum(entry.namespace == "tools.elastic_security" for entry in CATALOG) == 40
    assert set(loaded_catalog) == expected
    for action_name, (template, _) in loaded_catalog.items():
        assert template.definition.action == action_name


@pytest.mark.parametrize("entry", CATALOG, ids=lambda entry: entry.action)
def test_catalog_metadata_method_and_path(
    entry: CatalogEntry,
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, path = loaded_catalog[entry.action]
    definition = template.definition
    assert definition.namespace == entry.namespace
    assert definition.name == entry.name
    assert definition.display_group == entry.display_group
    assert str(definition.doc_url) == entry.doc_url

    step = http_step(template)
    assert step.args["method"] == entry.method

    source = path.read_text()
    for fragment in re.split(r"\{[^}]+\}", entry.endpoint_path):
        normalized_fragment = fragment.strip("/")
        if len(normalized_fragment) >= 2:
            assert normalized_fragment in source


def test_common_input_contracts(
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    for action_name, (template, _) in loaded_catalog.items():
        expects = template.definition.expects
        assert expects["base_url"].type == "str | None", action_name
        assert expects["verify_ssl"].type == "bool", action_name
        assert expects["verify_ssl"].default is True, action_name
        if (
            not action_name.startswith("tools.elasticsearch.")
            and action_name != "tools.elastic_security.list_detection_signals"
        ):
            assert expects["space_id"].type == "str | None", action_name
            assert expects["space_id"].default is None, action_name


def test_path_identifiers_are_url_encoded(
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    assert url_encode("document/with space") == "document%2Fwith%20space"
    for action in (
        "tools.elasticsearch.get_document",
        "tools.elastic_security.get_response_action",
    ):
        _, path = loaded_catalog[action]
        assert "FN.url_encode" in path.read_text()

    template, _ = loaded_catalog["tools.elasticsearch.eql"]
    build_path = execute_script(template, "build_path")
    assert build_path("logs-*/events") == "/logs-*%2Fevents/_eql/search"


def test_default_and_named_space_paths(
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = loaded_catalog["tools.elastic_security.search_detection_alerts"]
    build_path = execute_script(template, "build_path")
    endpoint = "/api/detection_engine/signals/search"
    assert build_path(endpoint, None) == endpoint
    assert build_path(endpoint, "blue team") == f"/s/blue%20team{endpoint}"


@pytest.mark.parametrize(
    ("action", "endpoint"),
    (
        (
            "tools.elastic_security.search_attack_discoveries",
            "/api/attack_discovery/_find",
        ),
        (
            "tools.elastic_security.search_entities",
            "/api/security/entity_store/entities",
        ),
        (
            "tools.elastic_security.run_osquery_live_query",
            "/api/osquery/live_queries",
        ),
    ),
)
def test_investigation_actions_support_default_and_named_spaces(
    action: str,
    endpoint: str,
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = loaded_catalog[action]
    build_path = execute_script(template, "build_path")
    assert build_path(None) == endpoint
    assert build_path("blue team") == f"/s/blue%20team{endpoint}"


@pytest.mark.parametrize(
    "action",
    (
        "tools.elasticsearch.eql",
        "tools.elasticsearch.esql",
        "tools.elastic_security.run_osquery_live_query",
        "tools.elastic_security.search_detection_alerts",
    ),
)
def test_native_payload_and_optional_params_pass_through(
    action: str,
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = loaded_catalog[action]
    expects = template.definition.expects
    assert expects["payload"].type == "dict[str, Any]"
    assert expects["params"].default is None
    request = http_step(template)
    assert request.args["payload"] == "${{ inputs.payload }}"
    assert request.args["params"] == "${{ inputs.params }}"


@pytest.mark.parametrize(
    "action",
    (
        "tools.elastic_security.search_attack_discoveries",
        "tools.elastic_security.search_entities",
    ),
)
def test_investigation_search_params_pass_through(
    action: str,
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = loaded_catalog[action]
    assert template.definition.expects["params"].default is None
    assert http_step(template).args["params"] == "${{ inputs.params }}"


def test_exception_container_and_item_list_actions_are_distinct(
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    container, _ = loaded_catalog["tools.elastic_security.list_exception_lists"]
    item, _ = loaded_catalog["tools.elastic_security.list_exception_list_items"]
    assert "containers and metadata" in container.definition.description
    assert "within" in item.definition.description
    assert http_step(container).args["url"] == http_step(item).args["url"]
    container_path = execute_script(container, "build_path")(None)
    item_path = execute_script(item, "build_path")(None)
    assert container_path != item_path


def test_import_export_and_binary_contracts(
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = loaded_catalog["tools.elastic_security.import_detection_rules"]
    request = http_step(template)
    assert (
        request.args["files"]["file"]["content_base64"]
        == "${{ inputs.base64_content }}"
    )

    template, _ = loaded_catalog["tools.elastic_security.export_detection_rules"]
    assert template.definition.returns == "${{ steps.request.result.data }}"

    template, _ = loaded_catalog["tools.elastic_security.download_response_file"]
    assert http_step(template).args["base64_encode_data"] is True


@pytest.mark.parametrize(
    ("action", "array_field"),
    (
        ("tools.elastic_security.upload_endpoint_file", "endpoint_ids"),
        ("tools.elastic_security.create_endpoint_script", "platform"),
    ),
)
def test_multipart_payload_assembly_is_mechanical(
    action: str,
    array_field: str,
    loaded_catalog: dict[str, tuple[TemplateAction, Path]],
) -> None:
    template, _ = loaded_catalog[action]
    build_form_data = execute_script(template, "build_form_data")
    result = build_form_data(
        {
            array_field: ["first", "second"],
            "parameters": {"overwrite": False},
            "requiresInput": False,
            "name": "Collect host data",
        }
    )
    assert result == {
        array_field: ["first", "second"],
        "parameters": '{"overwrite":false}',
        "requiresInput": "false",
        "name": "Collect host data",
    }
