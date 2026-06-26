from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

JIRA_TEMPLATE_DIR = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/jira"
)

ADF_DOC = {
    "version": 1,
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "ADF text"}],
        }
    ],
}


def _template(template_name: str) -> dict[str, Any]:
    return yaml.safe_load((JIRA_TEMPLATE_DIR / template_name).read_text())


def _build_payload_main(template_name: str):
    template = _template(template_name)
    for step in template["definition"]["steps"]:
        if step["ref"] == "build_payload":
            namespace: dict[str, Any] = {}
            exec(step["args"]["script"], namespace)
            return namespace["main"]
    raise AssertionError(f"{template_name} has no build_payload step")


def test_add_comment_wraps_plain_text_and_passes_native_payload() -> None:
    main = _build_payload_main("add_comment.yml")

    plain_payload = main("hello")
    assert plain_payload == {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "hello"}],
                }
            ],
        }
    }

    native_payload = {
        "body": ADF_DOC,
        "visibility": {"type": "role", "value": "Administrators"},
        "properties": [{"key": "source", "value": "tracecat"}],
    }
    assert main(native_payload) is native_payload


def test_add_comment_treats_json_strings_as_plain_text() -> None:
    main = _build_payload_main("add_comment.yml")

    adf_json = json.dumps(ADF_DOC)
    payload = main(adf_json)

    assert payload["body"]["content"][0]["content"][0]["text"] == adf_json


def test_create_issue_fields_keeps_existing_field_map_list_type() -> None:
    template = _template("create_issue.yml")

    expects = template["definition"]["expects"]
    assert expects["fields"]["type"] == "list[dict[str, Any]]"
    assert expects["fields"]["default"] == []
    assert expects["description"]["type"] == "str"

    fields_step = next(
        step for step in template["definition"]["steps"] if step["ref"] == "fields"
    )
    assert (
        fields_step["args"]["value"]
        == "${{ FN.merge([steps.required_fields.result, FN.merge(inputs.fields)]) }}"
    )


def test_update_issue_fields_keeps_existing_field_map_list_type() -> None:
    template = _template("update_issue_fields.yml")

    expects = template["definition"]["expects"]
    assert expects["fields"]["type"] == "list[dict[str, Any]]"

    update_step = next(
        step
        for step in template["definition"]["steps"]
        if step["ref"] == "update_issue"
    )
    assert update_step["args"]["payload"] == {
        "fields": "${{ FN.merge(inputs.fields) }}"
    }


def test_status_and_description_wrappers_stay_simple() -> None:
    status_template = _template("update_issue_status.yml")
    status_expects = status_template["definition"]["expects"]
    assert set(status_expects) == {"issue_id_or_key", "transition_id", "base_url"}

    description_template = _template("update_issue_description.yml")
    description_expects = description_template["definition"]["expects"]
    assert description_expects["description"]["type"] == "str"
