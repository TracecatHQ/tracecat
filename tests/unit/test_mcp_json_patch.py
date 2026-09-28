from __future__ import annotations

from typing import cast

import pytest
from fastmcp.exceptions import ToolError

from tracecat.mcp.json_patch import (
    apply_json_patch_operations,
    resolve_action_ref_path,
    validate_patch_paths,
)
from tracecat.mcp.schemas import JsonPatchOperation, JsonValue


def _op(**kwargs: object) -> JsonPatchOperation:
    return JsonPatchOperation.model_validate(kwargs)


def _obj(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _arr(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def test_validate_patch_paths_allows_editable_paths() -> None:
    patch_ops = [
        _op(op="replace", path="/metadata/title", value="Updated title"),
        _op(op="move", path="/layout/actions/0", **{"from": "/layout/actions/1"}),
    ]

    validate_patch_paths(
        patch_ops,
        allowed_top_level_paths={"metadata", "layout"},
    )


def test_validate_patch_paths_rejects_forbidden_path() -> None:
    with pytest.raises(ToolError, match="not editable via edit_workflow"):
        validate_patch_paths(
            [_op(op="replace", path="/version", value=2)],
            allowed_top_level_paths={"metadata", "layout"},
        )


def test_apply_json_patch_operations_replace_nested_value() -> None:
    document: dict[str, JsonValue] = {
        "metadata": {"title": "Original"},
        "layout": {"actions": []},
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[_op(op="replace", path="/metadata/title", value="Updated")],
    )

    assert _obj(patched["metadata"])["title"] == "Updated"
    assert _obj(document["metadata"])["title"] == "Original"


def test_apply_json_patch_operations_add_appends_to_array() -> None:
    document: dict[str, JsonValue] = {"layout": {"actions": [{"ref": "step_a"}]}}

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(
                op="add",
                path="/layout/actions/-",
                value={"ref": "step_b"},
            )
        ],
    )

    actions = _arr(_obj(patched["layout"])["actions"])
    assert _obj(actions[0])["ref"] == "step_a"
    assert _obj(actions[1])["ref"] == "step_b"


def test_apply_json_patch_operations_remove_deletes_array_item() -> None:
    document: dict[str, JsonValue] = {
        "layout": {"actions": [{"ref": "step_a"}, {"ref": "step_b"}]}
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[_op(op="remove", path="/layout/actions/0")],
    )

    actions = _arr(_obj(patched["layout"])["actions"])
    assert len(actions) == 1
    assert _obj(actions[0])["ref"] == "step_b"


def test_apply_json_patch_operations_move_and_copy_values() -> None:
    document: dict[str, JsonValue] = {
        "metadata": {"title": "Original", "description": ""},
        "layout": {"actions": [{"ref": "step_a"}, {"ref": "step_b"}]},
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(op="copy", path="/metadata/description", **{"from": "/metadata/title"}),
            _op(op="move", path="/layout/actions/0", **{"from": "/layout/actions/1"}),
        ],
    )

    assert _obj(patched["metadata"])["description"] == "Original"
    actions = _arr(_obj(patched["layout"])["actions"])
    assert _obj(actions[0])["ref"] == "step_b"
    assert _obj(actions[1])["ref"] == "step_a"


def test_apply_json_patch_operations_test_checks_expected_value() -> None:
    document: dict[str, JsonValue] = {"metadata": {"title": "Original"}}

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(op="test", path="/metadata/title", value="Original"),
            _op(op="replace", path="/metadata/title", value="Updated"),
        ],
    )

    assert _obj(patched["metadata"])["title"] == "Updated"


def test_apply_json_patch_operations_test_raises_on_mismatch() -> None:
    with pytest.raises(ToolError, match="Patch test operation failed"):
        apply_json_patch_operations(
            document=cast(dict[str, JsonValue], {"metadata": {"title": "Original"}}),
            patch_ops=[_op(op="test", path="/metadata/title", value="Different")],
        )


def test_apply_json_patch_operations_supports_json_pointer_escaping() -> None:
    document: dict[str, JsonValue] = {
        "metadata": {
            "config": {
                "a/b": "slash",
                "tilde~key": "tilde",
            }
        }
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(op="replace", path="/metadata/config/a~1b", value="updated slash"),
            _op(
                op="replace",
                path="/metadata/config/tilde~0key",
                value="updated tilde",
            ),
        ],
    )

    assert _obj(_obj(patched["metadata"])["config"]) == {
        "a/b": "updated slash",
        "tilde~key": "updated tilde",
    }


def test_apply_json_patch_operations_rejects_invalid_array_index() -> None:
    with pytest.raises(ToolError, match="Patch array index out of range"):
        apply_json_patch_operations(
            document=cast(dict[str, JsonValue], {"layout": {"actions": []}}),
            patch_ops=[_op(op="remove", path="/layout/actions/1")],
        )


@pytest.mark.parametrize(
    "patch_op",
    [
        _op(op="remove", path="/layout/actions/01"),
        _op(op="add", path="/layout/actions/01", value={"ref": "step_c"}),
    ],
)
def test_apply_json_patch_operations_rejects_non_canonical_array_index(
    patch_op: JsonPatchOperation,
) -> None:
    with pytest.raises(ToolError, match="Invalid array index"):
        apply_json_patch_operations(
            document=cast(
                dict[str, JsonValue],
                {"layout": {"actions": [{"ref": "step_a"}, {"ref": "step_b"}]}},
            ),
            patch_ops=[patch_op],
        )


def _actions_document() -> dict[str, JsonValue]:
    return {
        "definition": {
            "actions": [
                {"ref": "fetch_events", "action": "core.http_request", "args": {}},
                {
                    "ref": "classify",
                    "action": "core.transform.reshape",
                    "args": {"value": 1},
                },
                {
                    "ref": "build_alert",
                    "action": "core.transform.reshape",
                    "args": {"value": 2},
                },
            ]
        },
        "layout": {
            "actions": [
                {"ref": "fetch_events", "x": 0.0, "y": 0.0},
                {"ref": "classify", "x": 10.0, "y": 0.0},
                {"ref": "build_alert", "x": 20.0, "y": 0.0},
            ]
        },
    }


def _action_refs(document: dict[str, JsonValue]) -> list[JsonValue]:
    return [
        _obj(action)["ref"] for action in _arr(_obj(document["definition"])["actions"])
    ]


def test_resolve_action_ref_path_rewrites_ref_to_index() -> None:
    document = _actions_document()

    assert (
        resolve_action_ref_path(document, "/definition/actions/@classify")
        == "/definition/actions/1"
    )
    assert (
        resolve_action_ref_path(document, "/definition/actions/@build_alert/args/value")
        == "/definition/actions/2/args/value"
    )
    assert (
        resolve_action_ref_path(document, "/layout/actions/@classify/x")
        == "/layout/actions/1/x"
    )


def test_resolve_action_ref_path_leaves_numeric_and_other_paths_alone() -> None:
    document = _actions_document()

    assert (
        resolve_action_ref_path(document, "/definition/actions/0/args")
        == "/definition/actions/0/args"
    )
    assert resolve_action_ref_path(document, "/metadata/title") == "/metadata/title"
    assert resolve_action_ref_path(document, "/schedules/@x") == "/schedules/@x"


def test_resolve_action_ref_path_unknown_ref_names_ref_and_known_refs() -> None:
    with pytest.raises(ToolError, match="Unknown action ref 'missing'") as exc_info:
        resolve_action_ref_path(_actions_document(), "/definition/actions/@missing")
    assert "'fetch_events'" in str(exc_info.value)


def test_resolve_action_ref_path_append_only_for_bare_action_path() -> None:
    document = _actions_document()

    assert (
        resolve_action_ref_path(
            document, "/definition/actions/@new_step", append_if_missing=True
        )
        == "/definition/actions/-"
    )
    with pytest.raises(ToolError, match="Unknown action ref 'new_step'"):
        resolve_action_ref_path(
            document, "/definition/actions/@new_step/args", append_if_missing=True
        )


def test_apply_json_patch_operations_replace_nested_by_ref() -> None:
    patched = apply_json_patch_operations(
        document=_actions_document(),
        patch_ops=[
            _op(
                op="replace",
                path="/definition/actions/@build_alert/args/value",
                value=99,
            ),
            _op(op="test", path="/definition/actions/@classify/args/value", value=1),
        ],
    )

    actions = _arr(_obj(patched["definition"])["actions"])
    assert _obj(_obj(actions[2])["args"])["value"] == 99


def test_apply_json_patch_operations_add_by_missing_ref_appends() -> None:
    patched = apply_json_patch_operations(
        document=_actions_document(),
        patch_ops=[
            _op(
                op="add",
                path="/definition/actions/@notify_owner",
                value={"ref": "notify_owner", "action": "core.http_request"},
            ),
            _op(
                op="add",
                path="/layout/actions/@notify_owner",
                value={"ref": "notify_owner", "x": 30.0, "y": 0.0},
            ),
        ],
    )

    assert _action_refs(patched) == [
        "fetch_events",
        "classify",
        "build_alert",
        "notify_owner",
    ]
    layout = _arr(_obj(patched["layout"])["actions"])
    assert _obj(layout[-1])["ref"] == "notify_owner"


def test_apply_json_patch_operations_add_by_ref_requires_matching_value_ref() -> None:
    with pytest.raises(ToolError, match="must be 'notify_owner'"):
        apply_json_patch_operations(
            document=_actions_document(),
            patch_ops=[
                _op(
                    op="add",
                    path="/definition/actions/@notify_owner",
                    value={"ref": "other", "action": "core.http_request"},
                )
            ],
        )


def test_apply_json_patch_operations_remove_by_ref() -> None:
    patched = apply_json_patch_operations(
        document=_actions_document(),
        patch_ops=[
            _op(op="remove", path="/definition/actions/@classify"),
            _op(op="remove", path="/layout/actions/@classify"),
        ],
    )

    assert _action_refs(patched) == ["fetch_events", "build_alert"]
    layout = _arr(_obj(patched["layout"])["actions"])
    assert [_obj(entry)["ref"] for entry in layout] == ["fetch_events", "build_alert"]


def test_apply_json_patch_operations_reresolves_refs_after_index_shift() -> None:
    # Removing the first action shifts build_alert from index 2 to index 1; the
    # second op must resolve against the shifted document.
    patched = apply_json_patch_operations(
        document=_actions_document(),
        patch_ops=[
            _op(op="remove", path="/definition/actions/@fetch_events"),
            _op(
                op="replace",
                path="/definition/actions/@build_alert/args/value",
                value="shifted",
            ),
        ],
    )

    actions = _arr(_obj(patched["definition"])["actions"])
    assert _action_refs(patched) == ["classify", "build_alert"]
    assert _obj(_obj(actions[1])["args"])["value"] == "shifted"
    assert _obj(_obj(actions[0])["args"])["value"] == 1


def test_apply_json_patch_operations_move_and_copy_by_ref() -> None:
    patched = apply_json_patch_operations(
        document=_actions_document(),
        patch_ops=[
            _op(
                op="copy",
                path="/definition/actions/@build_alert/args/copied",
                **{"from": "/definition/actions/@classify/args/value"},
            ),
            _op(
                op="move",
                path="/definition/actions/@classify/args/moved",
                **{"from": "/definition/actions/@build_alert/args/value"},
            ),
        ],
    )

    actions = _arr(_obj(patched["definition"])["actions"])
    assert _obj(_obj(actions[2])["args"]) == {"copied": 1}
    assert _obj(_obj(actions[1])["args"]) == {"value": 1, "moved": 2}


def test_apply_json_patch_operations_move_whole_action_by_ref() -> None:
    # Moving fetch_events (index 0) to build_alert's slot: after the removal
    # build_alert sits at index 1, so the target resolves to 1, not 2.
    patched = apply_json_patch_operations(
        document=_actions_document(),
        patch_ops=[
            _op(
                op="move",
                path="/definition/actions/@build_alert",
                **{"from": "/definition/actions/@fetch_events"},
            ),
        ],
    )

    assert _action_refs(patched) == ["classify", "fetch_events", "build_alert"]


def test_apply_json_patch_operations_unknown_ref_in_from() -> None:
    with pytest.raises(ToolError, match="Unknown action ref 'ghost'"):
        apply_json_patch_operations(
            document=_actions_document(),
            patch_ops=[
                _op(
                    op="copy",
                    path="/definition/actions/@classify/args/x",
                    **{"from": "/definition/actions/@ghost/args/value"},
                )
            ],
        )
