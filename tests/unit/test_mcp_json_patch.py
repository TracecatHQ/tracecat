from __future__ import annotations

from typing import cast

import pytest
from fastmcp.exceptions import ToolError

from tracecat.mcp.json_patch import apply_json_patch_operations, validate_patch_paths
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
