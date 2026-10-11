from datetime import UTC, datetime
from uuid import uuid4

from tracecat.workflow.management.schemas import (
    WorkflowDefinitionRead,
    format_registry_origin,
)
from tracecat.workflow.management.utils import build_trigger_inputs_schema


def test_workflow_definition_read_accepts_legacy_flat_registry_lock():
    created_at = datetime.now(UTC)
    definition = WorkflowDefinitionRead.model_validate(
        {
            "id": uuid4(),
            "workflow_id": uuid4(),
            "workspace_id": uuid4(),
            "version": 1,
            "content": {},
            "registry_lock": {
                "tracecat_registry": "2025.12.10.123456",
                "git+ssh://deploy@example.com/acme/actions.git": "abcdef1234567890",
            },
            "created_at": created_at,
            "updated_at": created_at,
        }
    )

    assert definition.registry_lock is not None
    assert definition.registry_lock.origins == {
        "tracecat_registry": "2025.12.10.123456",
        "git+ssh://deploy@example.com/acme/actions.git": "abcdef1234567890",
    }
    assert definition.registry_lock.actions == {}
    assert definition.registry_lock_entries[0].label == "acme/actions@abcdef123456"
    assert definition.registry_lock_entries[1].label == (
        "tracecat_registry@2025.12.10.123456"
    )
    serialized = definition.model_dump(mode="json")
    assert "registry_lock" not in serialized
    assert serialized["registry_lock_entries"] == [
        {
            "origin": "git+ssh://deploy@example.com/acme/actions.git",
            "version": "abcdef1234567890",
            "label": "acme/actions@abcdef123456",
        },
        {
            "origin": "tracecat_registry",
            "version": "2025.12.10.123456",
            "label": "tracecat_registry@2025.12.10.123456",
        },
    ]


def test_format_registry_origin_accepts_non_git_ssh_users():
    origin = "git+ssh://deploy@example.com/acme/actions.git"

    assert format_registry_origin(origin) == "acme/actions"


def test_build_trigger_inputs_schema_generates_json_schema():
    expects = {
        "case_id": {"type": "str", "description": "Case identifier"},
        "severity": {"type": "enum['low','high']"},
        "count": {"type": "int", "default": 1},
    }

    schema = build_trigger_inputs_schema(expects)

    assert schema is not None
    assert schema["type"] == "object"
    # Fields with defaults should NOT be required
    assert set(schema.get("required", [])) == {"case_id", "severity"}

    properties = schema.get("properties", {})
    assert properties["case_id"]["type"] == "string"
    assert properties["case_id"]["description"] == "Case identifier"
    assert properties["count"]["type"] == "integer"
    assert properties["count"]["default"] == 1

    severity_schema = properties["severity"]
    assert severity_schema["enum"] == ["low", "high"]


def test_build_trigger_inputs_schema_honors_optional_metadata():
    expects = {
        "cursor": {
            "type": "str",
            "description": "Pagination cursor.",
            "optional": True,
        },
    }

    schema = build_trigger_inputs_schema(expects)

    assert schema is not None
    assert "cursor" not in schema.get("required", [])
    properties = schema.get("properties", {})
    assert properties["cursor"]["type"] == "string"
