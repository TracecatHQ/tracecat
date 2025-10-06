from tracecat.workflow.management.schemas import build_trigger_inputs_schema


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
