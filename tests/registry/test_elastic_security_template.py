"""Regression tests for the Elastic Security `list_detection_signals` template.

The template assembles the request body via an inline Python step so optional
inputs (notably `_source`) are omitted from the payload when not provided.
These tests pin that contract down without spinning up the executor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_PATH = (
    Path(
        "packages/tracecat-registry/tracecat_registry/templates/tools/elastic_security"
    )
    / "list_detection_signals.yml"
)

START = "2026-05-01T00:00:00Z"
END = "2026-05-02T00:00:00Z"
QUERY: dict[str, Any] = {"bool": {"must": [{"match_all": {}}]}}


@pytest.fixture(scope="module")
def template() -> TemplateAction:
    return TemplateAction.from_yaml(TEMPLATE_PATH)


@pytest.fixture(scope="module")
def build_payload(template: TemplateAction):
    step = next(s for s in template.definition.steps if s.ref == "build_search_payload")
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)  # noqa: S102
    return namespace["main"]


def test_expects_declares_optional_source_fields(template: TemplateAction) -> None:
    expects = template.definition.expects
    assert "source_fields" in expects, "source_fields input must be declared"
    field = expects["source_fields"]
    assert field.default is None
    # The type string is what surfaces in the UI/schema — keep it strict.
    assert field.type == "list[str] | dict[str, Any] | None"


def test_payload_omits_source_when_unset(build_payload) -> None:
    payload = build_payload(START, END, QUERY, 100, None)
    assert "_source" not in payload
    assert payload == {"start": START, "end": END, "query": QUERY, "size": 100}


def test_payload_includes_source_list(build_payload) -> None:
    fields = ["@timestamp", "kibana.alert.uuid", "kibana.alert.severity"]
    payload = build_payload(START, END, QUERY, 50, fields)
    assert payload["_source"] == fields
    assert payload["size"] == 50


def test_payload_includes_source_dict_with_includes_excludes(build_payload) -> None:
    spec = {"includes": ["kibana.alert.*"], "excludes": ["kibana.alert.rule.note"]}
    payload = build_payload(START, END, QUERY, 25, spec)
    assert payload["_source"] == spec


def test_payload_preserves_empty_source_list(build_payload) -> None:
    # An explicit empty list is a valid Elastic instruction to return no source
    # fields. It must not be silently dropped.
    payload = build_payload(START, END, QUERY, 10, [])
    assert payload["_source"] == []
