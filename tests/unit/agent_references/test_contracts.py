"""Pure contract conformance, including a consumer with no native runtime imports."""

import json
import subprocess
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from tracecat.agent.references.contracts import (
    AuthoredSource,
    ExecutionAuthority,
    LogicalArtifact,
    ReferenceCapabilities,
    ReferencePreparationFailure,
    ReferencePreparationInput,
    ResolvedReferenceSnapshot,
    SkillManifest,
)
from tracecat.agent.references.markdown import (
    parse_markdown_references,
    scan_markdown_files,
)
from tracecat.agent.references.types import ReferenceDiagnostic
from tracecat.agent.references.uri import (
    ReferenceDiagnosticCode,
    ReferenceKind,
    ReferenceTarget,
    ReferenceURIError,
    parse_reference_uri,
    serialize_reference_uri,
)

FIXTURES = json.loads(
    (
        Path(__file__).parents[2] / "fixtures/agent_references/conformance.json"
    ).read_text()
)


@pytest.mark.parametrize("case", FIXTURES["uris"], ids=lambda c: c["name"])
def test_uri_conformance(case):
    assert (case["error"] is None) != (case["canonical"] is None)
    if case["error"]:
        with pytest.raises(ReferenceURIError) as exc:
            parse_reference_uri(case["uri"])
        assert exc.value.code == case["error"]
    else:
        assert (
            serialize_reference_uri(parse_reference_uri(case["uri"]))
            == case["canonical"]
        )


@pytest.mark.parametrize("case", FIXTURES["markdown"], ids=lambda c: c["name"])
def test_markdown_conformance(case):
    result = parse_markdown_references(case["source"], path="references/guide.md")
    assert [serialize_reference_uri(r.target) for r in result.references] == case[
        "targets"
    ]
    assert [d.code.value for d in result.diagnostics] == case["errors"]
    assert [[r.location.line, r.location.column] for r in result.references] == case[
        "locations"
    ]
    assert all(r.location.path == "references/guide.md" for r in result.references)
    assert all(d.location is not None for d in result.diagnostics)
    assert [
        [d.location.line, d.location.column] for d in result.diagnostics if d.location
    ] == case["diagnostic_locations"]
    assert all(
        d.location and d.location.path == "references/guide.md"
        for d in result.diagnostics
    )


def test_all_markdown_files_and_binary_rejection():
    link = b"[x](tracecat-ref://v1/tool/core.http_request)"
    result = scan_markdown_files(
        {
            "SKILL.md": b"plain",
            "references/nested.MD": link,
            "example.py": link,
            "bad.md": b"\xff",
        }
    )
    assert len(result.references) == 1
    assert result.references[0].location.path == "references/nested.MD"
    assert result.diagnostics[0].code == "invalid_source"


def test_multiple_links_and_multiline_locations():
    t = "tracecat-ref://v1/tool/core.http_request"
    result = parse_markdown_references(f"é😀 [one]({t}) [two]({t})\r\n> [three]({t})")
    assert [(r.location.line, r.location.column) for r in result.references] == [
        (1, 4),
        (1, 4 + len(f"[one]({t}) ")),
        (2, 3),
    ]


def test_tool_free_authority_cannot_activate_references():
    with pytest.raises(ValidationError):
        ExecutionAuthority(
            actor_id=None,
            session_mode="tool_free",
            activate_references=True,
            action_scope_ceiling=(),
            namespace_ceiling=None,
            admission_policy_hash="0" * 64,
        )


@pytest.mark.parametrize("path", ["/tmp/skill", "../skill", "a/../b", "a\\b", "a//b"])
def test_artifact_paths_are_logical(path):
    with pytest.raises(ValidationError):
        LogicalArtifact(
            key="bundle/key",
            path=path,
            content_hash="0" * 64,
            size_bytes=1,
            media_type="text/markdown",
        )


def test_preparation_roundtrip_and_context_separation():
    uid = UUID("11111111-1111-4111-8111-111111111111")
    data = {
        "organization_id": uid,
        "workspace_id": uid,
        "session_id": uid,
        "logical_turn_id": uid,
        "backend_id": "other",
        "harness_type": "independent",
        "authored": {
            "root": {
                "origin": "agent_instructions",
                "owner": ReferenceTarget(ReferenceKind.AGENT, str(uid)),
                "version_id": uid,
                "path": "instructions.md",
                "content_hash": "0" * 64,
                "markdown": "Hello",
            },
            "explicit": {},
        },
        "authority": {
            "actor_id": uid,
            "session_mode": "delegated_preset",
            "activate_references": True,
            "action_scope_ceiling": None,
            "namespace_ceiling": None,
            "admission_policy_hash": "0" * 64,
        },
        "input_hash": "0" * 64,
    }
    request = ReferencePreparationInput.model_validate(data)
    assert (
        ReferencePreparationInput.model_validate_json(request.model_dump_json())
        == request
    )
    for invalid in ["instructions", "actions", "role", "prompt_context", "credential"]:
        with pytest.raises(ValidationError):
            ReferencePreparationInput.model_validate({**data, invalid: "untrusted"})
    with pytest.raises(ValidationError):
        ReferencePreparationInput.model_validate({**data, "schema_version": 2})


def test_independent_consumer_imports_no_workflow_or_native_sdk():
    # A new interpreter avoids imports by the repository-wide pytest fixtures.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tracecat.agent.references.contracts import ReferenceSnapshotRef, RuntimeReferenceBinding; import sys; assert not any(k.startswith(('claude_agent_sdk', 'tracecat_ee.agent.workflows', 'tracecat.agent.runtime')) for k in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_capabilities_are_explicit_and_default_to_no_native_readiness():
    capability = ReferenceCapabilities(
        harness_type="independent", kinds=frozenset({ReferenceKind.SKILL})
    )
    assert not capability.eager_callable_readiness
    assert not capability.direct_child_delegation


@pytest.mark.parametrize(
    "kind", [kind for kind in ReferenceKind if kind != ReferenceKind.SKILL]
)
def test_skill_manifest_rejects_other_reference_kinds(kind):
    target = ReferenceTarget(
        kind,
        "core.http_request"
        if kind == ReferenceKind.TOOL
        else "11111111-1111-4111-8111-111111111111",
        "lookup" if kind == ReferenceKind.MCP_TOOL else None,
    )
    with pytest.raises(
        ValidationError, match="Skill manifests require a skill reference"
    ):
        SkillManifest.model_validate(
            {
                "skill": {
                    "target": target,
                    "version_id": "11111111-1111-4111-8111-111111111111",
                },
                "manifest_hash": "0" * 64,
                "files": [],
            }
        )


def snapshot_data():
    uid = "11111111-1111-4111-8111-111111111111"
    return {
        "snapshot_id": uid,
        "workspace_id": uid,
        "logical_turn_id": uid,
        "backend_id": "other",
        "harness_type": "independent",
        "input_hash": "0" * 64,
        "scopes": [
            {
                "key": "root",
                "selected": [],
                "edges": [],
                "required_tool_keys": ["lookup"],
                "policy_hash": "0" * 64,
                "registry_lock": {
                    "origins": {"synthetic": "v1"},
                    "actions": {"core.http_request": "synthetic"},
                },
                "callables": [
                    {
                        "key": "lookup",
                        "target": {
                            "target": {"kind": "tool", "identity": "core.http_request"}
                        },
                        "description": "Synthetic lookup",
                        "requires_approval": True,
                        "input_schema": {
                            "type": "object",
                            "properties": {"name": {"enum": ["original"]}},
                        },
                        "output_schema": {"type": "array", "items": {"type": "string"}},
                    }
                ],
            }
        ],
    }


def test_snapshot_owns_deeply_immutable_serializable_values():
    data = snapshot_data()
    snapshot = ResolvedReferenceSnapshot.model_validate(data)
    original_json = snapshot.model_dump_json()
    source_scope = data["scopes"][0]
    source_scope["callables"][0]["input_schema"]["properties"]["name"]["enum"].append(
        "changed"
    )
    source_scope["registry_lock"]["origins"]["synthetic"] = "v2"
    scope = snapshot.scopes[0]
    # Deliberately try mutations that the public read-only types also forbid.
    input_schema = cast(
        dict[str, dict[str, dict[str, list[str]]]], scope.callables[0].input_schema
    )
    output_schema = cast(dict[str, dict[str, str]], scope.callables[0].output_schema)
    with pytest.raises(TypeError):
        input_schema["properties"]["name"]["enum"][0] = "changed"
    with pytest.raises(TypeError):
        input_schema["properties"]["name"]["enum"] = ["changed"]
    with pytest.raises(TypeError):
        output_schema["items"]["type"] = "number"
    with pytest.raises(TypeError):
        cast(dict[str, str], scope.registry_lock.origins)["synthetic"] = "v3"
    with pytest.raises(TypeError):
        cast(dict[str, str], scope.registry_lock.origin_fingerprints)["synthetic"] = (
            "changed"
        )
    with pytest.raises(ValidationError):
        scope.registry_lock.origins = {"synthetic": "v4"}
    assert snapshot.model_dump_json() == original_json
    restored = ResolvedReferenceSnapshot.model_validate_json(original_json)
    assert restored == snapshot
    with pytest.raises(TypeError):
        cast(dict[str, str], restored.scopes[0].registry_lock.actions)[
            "core.http_request"
        ] = "changed"
    wire = snapshot.model_dump(mode="json")
    wire["scopes"][0]["callables"][0]["input_schema"]["properties"]["name"][
        "enum"
    ].append("changed")
    assert snapshot.model_dump_json() == original_json


@pytest.mark.parametrize("invalid", ["extra", "origin", "fingerprint"])
def test_snapshot_rejects_invalid_registry_locks(invalid):
    data = snapshot_data()
    lock = data["scopes"][0]["registry_lock"]
    if invalid == "extra":
        lock["unexpected"] = "value"
    elif invalid == "origin":
        lock["actions"]["core.http_request"] = "missing"
    else:
        lock["origin_fingerprints"] = {"missing": "fingerprint"}
    with pytest.raises(ValidationError):
        ResolvedReferenceSnapshot.model_validate(data)


@pytest.mark.parametrize("value", [b"text", {1, 2}, (1, 2), object()])
def test_callable_schemas_reject_non_json_python_values(value):
    data = snapshot_data()
    data["scopes"][0]["callables"][0]["input_schema"]["invalid"] = value
    with pytest.raises(ValidationError):
        ResolvedReferenceSnapshot.model_validate(data)


@pytest.mark.parametrize("path", ["/tmp/skill", "../skill", "a/../b", "a\\b", "a//b"])
def test_authored_source_paths_are_logical(path):
    uid = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(ValidationError):
        AuthoredSource.model_validate(
            {
                "origin": "skill_file",
                "owner": {"kind": "skill", "identity": uid},
                "version_id": uid,
                "path": path,
                "content_hash": "0" * 64,
                "markdown": "text",
            }
        )


def test_preparation_failure_without_authored_source_location():
    failure = ReferencePreparationFailure(
        diagnostics=(ReferenceDiagnostic(ReferenceDiagnosticCode.NOT_READY),)
    )
    assert failure.diagnostics[0].location is None
    assert (
        ReferencePreparationFailure.model_validate_json(failure.model_dump_json())
        == failure
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_callable_schemas_reject_nested_nonfinite_numbers(value, field):
    data = snapshot_data()
    data["scopes"][0]["callables"][0][field] = {"nested": {"enum": [value]}}
    with pytest.raises(ValidationError):
        ResolvedReferenceSnapshot.model_validate(data)


def test_callable_finite_numbers_roundtrip():
    data = snapshot_data()
    data["scopes"][0]["callables"][0]["input_schema"] = {
        "enum": [0, -2, 1.25, 1e100, True, None]
    }
    snapshot = ResolvedReferenceSnapshot.model_validate(data)
    assert (
        ResolvedReferenceSnapshot.model_validate_json(snapshot.model_dump_json())
        == snapshot
    )


@pytest.mark.parametrize("value", ["yes", "false", "true", 0, 1])
def test_authority_wire_rejects_coerced_activation(value):
    data = {
        "actor_id": None,
        "session_mode": "delegated_preset",
        "activate_references": value,
        "action_scope_ceiling": None,
        "namespace_ceiling": None,
        "admission_policy_hash": "0" * 64,
    }
    with pytest.raises(ValidationError):
        ExecutionAuthority.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("value", [True, 1.0, "1", 2])
@pytest.mark.parametrize(
    "field", ["schema_version", "snapshot_versions", "binding_versions"]
)
def test_capability_wire_requires_supported_integer_versions(value, field):
    data = {
        "harness_type": "independent",
        "kinds": ["skill"],
        field: value if field == "schema_version" else [value],
    }
    with pytest.raises(ValidationError):
        ReferenceCapabilities.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "field", ["direct_child_delegation", "eager_callable_readiness"]
)
def test_capability_wire_rejects_coerced_readiness(field):
    with pytest.raises(ValidationError):
        ReferenceCapabilities.model_validate_json(
            json.dumps(
                {"harness_type": "independent", "kinds": ["skill"], field: "yes"}
            )
        )


def test_callable_wire_requires_boolean_approval():
    data = snapshot_data()
    data["scopes"][0]["callables"][0]["requires_approval"] = "false"
    with pytest.raises(ValidationError):
        ResolvedReferenceSnapshot.model_validate_json(json.dumps(data))
