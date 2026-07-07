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


def test_apply_json_patch_ref_addressed_replace() -> None:
    """A ref token addresses an action by its ``ref`` field, not by index."""
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {"ref": "step_a", "args": {"x": 1}},
                {"ref": "step_b", "args": {"x": 2}},
            ]
        }
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(op="replace", path="/definition/actions/step_b/args/x", value=99)
        ],
    )

    actions = _arr(_obj(patched["definition"])["actions"])
    assert _obj(_obj(actions[1])["args"])["x"] == 99
    assert _obj(_obj(actions[0])["args"])["x"] == 1


def test_apply_json_patch_ref_addressed_remove_and_from() -> None:
    """Ref tokens work for remove and for ``move``/``copy`` ``from`` paths."""
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {"ref": "step_a", "args": {"x": 1}},
                {"ref": "step_b", "args": {"x": 2}},
                {"ref": "step_c", "args": {"x": 3}},
            ]
        }
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(
                op="copy",
                path="/definition/actions/-",
                **{"from": "/definition/actions/step_a"},
            ),
            _op(op="remove", path="/definition/actions/step_b"),
        ],
    )

    refs = [_obj(a)["ref"] for a in _arr(_obj(patched["definition"])["actions"])]
    assert refs == ["step_a", "step_c", "step_a"]


def test_apply_json_patch_rejects_numeric_action_index() -> None:
    """A numeric index into /definition/actions with no matching ref is rejected."""
    document: dict[str, JsonValue] = {
        "definition": {"actions": [{"ref": "step_a"}, {"ref": "step_b"}]}
    }

    with pytest.raises(ToolError, match="Address actions by ref"):
        apply_json_patch_operations(
            document=document,
            patch_ops=[_op(op="remove", path="/definition/actions/0")],
        )


def test_apply_json_patch_append_action_still_allowed() -> None:
    document: dict[str, JsonValue] = {"definition": {"actions": [{"ref": "step_a"}]}}
    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(op="add", path="/definition/actions/-", value={"ref": "step_b"})
        ],
    )
    refs = [_obj(a)["ref"] for a in _arr(_obj(patched["definition"])["actions"])]
    assert refs == ["step_a", "step_b"]


def test_apply_json_patch_ref_not_found_raises() -> None:
    document: dict[str, JsonValue] = {"definition": {"actions": [{"ref": "step_a"}]}}
    with pytest.raises(ToolError, match="ref not found in array"):
        apply_json_patch_operations(
            document=document,
            patch_ops=[_op(op="replace", path="/definition/actions/ghost", value={})],
        )


def test_apply_json_patch_all_digit_ref_resolves_by_ref() -> None:
    """An all-digit token prefers a ref-match over numeric-index interpretation.

    ``slugify("1") == "1"`` and MCP edit documents accept arbitrary slug refs,
    so an action can legitimately have ref ``"1"``. When such a ref exists,
    ``/definition/actions/1/...`` must address that action by ref, not index 1.
    """
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {"ref": "1", "args": {"x": 1}},
                {"ref": "step_b", "args": {"x": 2}},
            ]
        }
    }
    # replace on a subpath: targets the action whose ref is "1" (index 0), NOT
    # index 1 (step_b).
    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[_op(op="replace", path="/definition/actions/1/args/x", value=99)],
    )
    actions = _arr(_obj(patched["definition"])["actions"])
    assert _obj(_obj(actions[0])["args"])["x"] == 99
    assert _obj(_obj(actions[1])["args"])["x"] == 2


def test_apply_json_patch_all_digit_ref_remove_resolves_by_ref() -> None:
    """Remove on an all-digit ref removes the ref-matched element, not the index."""
    document: dict[str, JsonValue] = {
        "definition": {"actions": [{"ref": "step_a"}, {"ref": "1"}, {"ref": "step_c"}]}
    }
    # ref "1" is at index 1 here too, but resolution is by ref: removing "/1"
    # removes the action whose ref is "1".
    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[_op(op="remove", path="/definition/actions/1")],
    )
    refs = [_obj(a)["ref"] for a in _arr(_obj(patched["definition"])["actions"])]
    assert refs == ["step_a", "step_c"]


def test_apply_json_patch_all_digit_ref_beats_index_position() -> None:
    """Ref-match wins even when the ref sits at a different index than its value."""
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {"ref": "step_a"},
                {"ref": "step_b"},
                {"ref": "1"},
            ]
        }
    }
    # "/1" would be index 1 (step_b) numerically, but ref "1" is at index 2.
    # Ref-preference resolves to index 2.
    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[_op(op="remove", path="/definition/actions/1")],
    )
    refs = [_obj(a)["ref"] for a in _arr(_obj(patched["definition"])["actions"])]
    assert refs == ["step_a", "step_b"]


def test_apply_json_patch_all_digit_add_subpath_resolves_by_ref() -> None:
    """An all-digit ref resolves for add on a subpath under that action."""
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {"ref": "01", "args": {}},
                {"ref": "step_b", "args": {}},
            ]
        }
    }
    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(op="add", path="/definition/actions/01/args/new", value="v"),
        ],
    )
    actions = _arr(_obj(patched["definition"])["actions"])
    assert _obj(_obj(actions[0])["args"])["new"] == "v"


def test_apply_json_patch_numeric_index_when_no_ref_collides() -> None:
    """An all-digit token with no matching ref into /definition/actions is rejected."""
    document: dict[str, JsonValue] = {
        "definition": {"actions": [{"ref": "step_a"}, {"ref": "step_b"}]}
    }
    with pytest.raises(ToolError, match="Address actions by ref"):
        apply_json_patch_operations(
            document=document,
            patch_ops=[_op(op="remove", path="/definition/actions/1")],
        )


def test_apply_json_patch_leading_zero_ref_resolves_by_ref() -> None:
    """A leading-zero token like ``01`` resolves by ref when a ref ``01`` exists."""
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {"ref": "step_a", "args": {"x": 1}},
                {"ref": "01", "args": {"x": 2}},
            ]
        }
    }
    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[_op(op="replace", path="/definition/actions/01/args/x", value=42)],
    )
    actions = _arr(_obj(patched["definition"])["actions"])
    assert _obj(_obj(actions[1])["args"])["x"] == 42


def test_apply_json_patch_leading_zero_no_ref_still_errors() -> None:
    """A leading-zero token with no matching ref into actions is rejected by ref."""
    document: dict[str, JsonValue] = {
        "definition": {"actions": [{"ref": "step_a"}, {"ref": "step_b"}]}
    }
    with pytest.raises(ToolError, match="Address actions by ref"):
        apply_json_patch_operations(
            document=document,
            patch_ops=[_op(op="remove", path="/definition/actions/01")],
        )


def test_apply_json_patch_nested_arrays_do_not_resolve_refs() -> None:
    document: dict[str, JsonValue] = {
        "definition": {
            "actions": [
                {
                    "ref": "step",
                    "args": {
                        "items": [
                            {"ref": "1", "name": "first"},
                            {"ref": "x", "name": "second"},
                        ]
                    },
                }
            ]
        }
    }

    patched = apply_json_patch_operations(
        document=document,
        patch_ops=[
            _op(
                op="replace",
                path="/definition/actions/step/args/items/1/name",
                value="updated",
            )
        ],
    )

    actions = _arr(_obj(patched["definition"])["actions"])
    items = _arr(_obj(_obj(actions[0])["args"])["items"])
    assert _obj(items[0])["name"] == "first"
    assert _obj(items[1])["name"] == "updated"
