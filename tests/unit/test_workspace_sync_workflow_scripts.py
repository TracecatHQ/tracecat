"""Workflow Python scripts are synced as their own files.

A ``core.script.run_python`` action's script is written to
``workflows/<Title>/scripts/<action ref>.py`` instead of inside
``definition.yml``, so people can read it as plain Python in Git.
"""

from __future__ import annotations

import yaml

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.package import file_names_by_resource
from tracecat.workspace_sync.resources import (
    parse_workspace_spec_files,
    serialize_workspace_spec_files,
)
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceSpec,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.workflow import serialize_workflow_spec

ENRICH_SCRIPT = "def main(ip):\n    return {'ip': ip}\n"
SCORE_SCRIPT = "def main(data):\n    return len(data)"

DEFINITION = "workflows/Triage-alerts/definition.yml"
ENRICH_PATH = "workflows/Triage-alerts/scripts/enrich_ip.py"
SCORE_PATH = "workflows/Triage-alerts/scripts/score.py"


def _workflow(
    score_ref: str = "score", score_args: dict[str, object] | None = None
) -> WorkflowResourceSpec:
    """Build the test workflow; tests vary only the ``score`` action."""
    return WorkflowResourceSpec(
        id="wf_4f2a",
        alias="triage",
        definition=DSLInput(
            title="Triage alerts",
            description="Scripts test workflow",
            entrypoint=DSLEntrypoint(ref="enrich_ip", expects={}),
            actions=[
                ActionStatement(
                    ref="enrich_ip",
                    action=PlatformAction.RUN_PYTHON,
                    args={"script": ENRICH_SCRIPT, "inputs": {"ip": "10.0.0.1"}},
                ),
                ActionStatement(
                    ref=score_ref,
                    action=PlatformAction.RUN_PYTHON,
                    args={"script": SCORE_SCRIPT} if score_args is None else score_args,
                    depends_on=["enrich_ip"],
                ),
                ActionStatement(
                    ref="notify",
                    action="core.transform.passthrough",
                    # Only run_python scripts are split out.
                    args={"value": "x", "script": "not python"},
                    depends_on=[score_ref],
                ),
            ],
        ),
    )


def _spec() -> WorkspaceSpec:
    return WorkspaceSpec(workflows={"wf_4f2a": _workflow()})


def _serialize(spec: WorkspaceSpec) -> dict[str, str]:
    return serialize_workspace_spec_files(
        manifest=WorkspaceManifest(),
        spec=spec,
        manifest_filename=MANIFEST_FILENAME,
        manifest_serializer=canonical_json_text,
    )


def _parse(files: dict[str, str]) -> WorkspaceSpec:
    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())
    assert parsed.diagnostics == []
    return parsed.spec


def _args_by_ref(content: str) -> dict[str, dict[str, object]]:
    data = yaml.safe_load(content)
    return {action["ref"]: action["args"] for action in data["definition"]["actions"]}


def test_push_writes_each_script_to_its_own_file() -> None:
    files = _serialize(_spec())

    assert files[ENRICH_PATH] == ENRICH_SCRIPT
    # Written byte for byte, even without a trailing newline.
    assert files[SCORE_PATH] == SCORE_SCRIPT
    args = _args_by_ref(files[DEFINITION])
    assert args == {
        "enrich_ip": {"inputs": {"ip": "10.0.0.1"}},
        "score": {},
        "notify": {"value": "x", "script": "not python"},
    }


def test_round_trip_through_files_preserves_scripts() -> None:
    spec = _spec()

    assert _parse(_serialize(spec)) == spec


def test_pull_reads_a_script_edited_in_git() -> None:
    files = _serialize(_spec())
    files[ENRICH_PATH] = "def main(ip):\n    return ip\n"

    actions = _parse(files).workflows["wf_4f2a"].definition.actions

    assert actions[0].args["script"] == "def main(ip):\n    return ip\n"


def test_scripts_move_with_a_renamed_workflow_folder() -> None:
    files = {
        path.replace("Triage-alerts", "Renamed"): content
        for path, content in _serialize(_spec()).items()
    }

    assert _parse(files) == _spec()


def test_inline_script_from_older_repositories_still_parses() -> None:
    # Before scripts were split out, definition.yml held them inline.
    workflow = _workflow()
    legacy = yaml.safe_load(serialize_workflow_spec(workflow))
    legacy["definition"]["actions"][0]["args"]["script"] = ENRICH_SCRIPT
    files = {
        DEFINITION: yaml.safe_dump(legacy, sort_keys=False),
        SCORE_PATH: SCORE_SCRIPT,
        # An inline script wins over a stray file for the same action.
        ENRICH_PATH: "def main():\n    return 'stale'\n",
    }

    assert _parse(files) == _spec()


def test_files_outside_the_script_layout_are_ignored() -> None:
    files = _serialize(_spec())
    files["workflows/Triage-alerts/scripts/README.md"] = "# notes"
    files["workflows/Triage-alerts/scripts/nested/helper.py"] = "x = 1"
    files["workflows/Triage-alerts/notes.py"] = "x = 1"

    assert _parse(files) == _spec()


def test_renaming_a_ref_without_its_script_file_fails_the_pull() -> None:
    # The leftover file is the only sign; importing would drop the script.
    files = _serialize(_spec())
    files[DEFINITION] = serialize_workflow_spec(_workflow(score_ref="score_alert"))

    parsed = parse_workspace_spec_files(files, manifest=WorkspaceManifest())

    assert [(d.workflow_path, d.error_type) for d in parsed.diagnostics] == [
        (SCORE_PATH, "validation")
    ]


def test_python_action_without_a_script_pulls_cleanly() -> None:
    # A draft Python action with no script yet pushes no file, and pulls back
    # exactly as it was pushed.
    spec = WorkspaceSpec(workflows={"wf_4f2a": _workflow(score_args={})})
    files = _serialize(spec)

    assert SCORE_PATH not in files
    assert _parse(files) == spec


def test_package_selection_includes_script_files() -> None:
    spec = _spec()
    files = _serialize(spec)
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=spec,
        files=files,
        paths={SyncResourceType.WORKFLOW: {"wf_4f2a": DEFINITION}},
    )

    names = file_names_by_resource(projection)

    workflow_files = {
        name[0]
        for key, entries in names.items()
        if key.resource_type is SyncResourceType.WORKFLOW
        for name in entries
    }
    assert {DEFINITION, ENRICH_PATH, SCORE_PATH} <= workflow_files
