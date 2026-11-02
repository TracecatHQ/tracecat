"""Minimal RFC 6902 JSON Patch helpers for MCP tools."""

from __future__ import annotations

import copy
from collections.abc import Sequence, Set

from fastmcp.exceptions import ToolError

from tracecat.mcp.schemas import JsonPatchOperation, JsonValue

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]
type JsonContainer = JsonObject | JsonArray


def patch_top_level(path: str) -> str:
    """Return the top-level JSON pointer token for a patch path."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ToolError(f"Patch paths must start with '/': {path!r}")
    parts = path.split("/")
    if len(parts) < 2 or not parts[1]:
        raise ToolError("Patching the document root is not supported")
    return parts[1].replace("~1", "/").replace("~0", "~")


def validate_patch_paths(
    patch_ops: Sequence[JsonPatchOperation],
    *,
    allowed_top_level_paths: Set[str],
) -> None:
    """Reject JSON Patch paths outside the allowed top-level document sections."""
    for patch_op in patch_ops:
        for path in (patch_op.path, patch_op.from_):
            if path is None:
                continue
            top_level = patch_top_level(path)
            if top_level not in allowed_top_level_paths:
                raise ToolError(
                    f"Patch path '{path}' is not editable via edit_workflow"
                )


def _decode_json_pointer(path: str) -> list[str]:
    """Decode a JSON Pointer path into unescaped tokens."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ToolError(f"Patch paths must start with '/': {path!r}")
    if path == "/":
        raise ToolError("Patching the document root is not supported")
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]


def _json_pointer_array_index(
    token: str,
    *,
    length: int,
    allow_end: bool = False,
) -> int:
    """Convert an array JSON Pointer token to an integer index."""
    if allow_end and token == "-":
        return length
    if token != "0" and (
        not token.isascii() or not token.isdigit() or token.startswith("0")
    ):
        raise ToolError(f"Invalid array index in patch path: {token!r}")
    index = int(token)
    max_index = length if allow_end else length - 1
    if index < 0 or index > max_index:
        raise ToolError(f"Patch array index out of range: {token!r}")
    return index


def _json_pointer_get(document: JsonValue, path: str) -> JsonValue:
    """Resolve a JSON Pointer path against a document."""
    current = document
    for token in _decode_json_pointer(path):
        if isinstance(current, dict):
            if token not in current:
                raise ToolError(f"Patch path not found: {path!r}")
            current = current[token]
        elif isinstance(current, list):
            index = _json_pointer_array_index(token, length=len(current))
            current = current[index]
        else:
            raise ToolError(f"Patch path not found: {path!r}")
    return current


def _json_pointer_get_parent(
    document: JsonValue, path: str
) -> tuple[JsonContainer, str]:
    """Resolve the parent container for a JSON Pointer path."""
    tokens = _decode_json_pointer(path)
    if not tokens:
        raise ToolError("Patching the document root is not supported")
    parent = document
    for token in tokens[:-1]:
        if isinstance(parent, dict):
            if token not in parent:
                raise ToolError(f"Patch path not found: {path!r}")
            parent = parent[token]
        elif isinstance(parent, list):
            index = _json_pointer_array_index(token, length=len(parent))
            parent = parent[index]
        else:
            raise ToolError(f"Patch path not found: {path!r}")
    if not isinstance(parent, (dict, list)):
        raise ToolError(f"Patch path not found: {path!r}")
    return parent, tokens[-1]


def _json_pointer_add(document: JsonValue, path: str, value: JsonValue) -> None:
    """Apply a JSON Patch add operation."""
    parent, token = _json_pointer_get_parent(document, path)
    if isinstance(parent, dict):
        parent[token] = value
        return
    if isinstance(parent, list):
        index = _json_pointer_array_index(token, length=len(parent), allow_end=True)
        parent.insert(index, value)
        return
    raise ToolError(f"Patch path not found: {path!r}")


def _json_pointer_remove(document: JsonValue, path: str) -> JsonValue:
    """Apply a JSON Patch remove operation and return the removed value."""
    parent, token = _json_pointer_get_parent(document, path)
    if isinstance(parent, dict):
        if token not in parent:
            raise ToolError(f"Patch path not found: {path!r}")
        return parent.pop(token)
    if isinstance(parent, list):
        index = _json_pointer_array_index(token, length=len(parent))
        return parent.pop(index)
    raise ToolError(f"Patch path not found: {path!r}")


def _json_pointer_replace(document: JsonValue, path: str, value: JsonValue) -> None:
    """Apply a JSON Patch replace operation."""
    parent, token = _json_pointer_get_parent(document, path)
    if isinstance(parent, dict):
        if token not in parent:
            raise ToolError(f"Patch path not found: {path!r}")
        parent[token] = value
        return
    if isinstance(parent, list):
        index = _json_pointer_array_index(token, length=len(parent))
        parent[index] = value
        return
    raise ToolError(f"Patch path not found: {path!r}")


def _encode_json_pointer(tokens: Sequence[str]) -> str:
    """Encode tokens into a JSON Pointer path."""
    return "/" + "/".join(
        token.replace("~", "~0").replace("/", "~1") for token in tokens
    )


# Array sections whose elements carry a unique ``ref`` and may be addressed as
# ``/<section>/actions/@<ref>`` instead of by numeric index.
_REF_ADDRESSABLE_SECTIONS = frozenset({"definition", "layout"})
_REF_TOKEN_PREFIX = "@"


def _ref_addressed_action_refs(document: JsonValue, section: str) -> list[str]:
    """Return the ``ref`` of every element in ``/<section>/actions``."""
    if not isinstance(document, dict):
        return []
    container = document.get(section)
    if not isinstance(container, dict):
        return []
    actions = container.get("actions")
    if not isinstance(actions, list):
        return []
    refs: list[str] = []
    for action in actions:
        if isinstance(action, dict):
            ref = action.get("ref")
            if isinstance(ref, str):
                refs.append(ref)
    return refs


def resolve_action_ref_path(
    document: JsonValue,
    path: str,
    *,
    append_if_missing: bool = False,
) -> str:
    """Rewrite ``/definition/actions/@<ref>[/...]`` to its numeric index form.

    The ref is resolved against ``document`` as it stands now, so callers must
    resolve each operation right before applying it. ``/layout/actions/@<ref>``
    resolves the same way. Numeric and non-``@`` paths are returned unchanged.

    Args:
        document: The document the path will be applied to.
        path: A JSON Pointer path, possibly using the ``@<ref>`` token.
        append_if_missing: When the ref does not exist and the path addresses
            the action element itself (no suffix), return the ``/-`` append
            path instead of raising. Used by ``add``.

    Raises:
        ToolError: If the ref is unknown (and appending is not allowed).
    """
    tokens = _decode_json_pointer(path)
    if (
        len(tokens) < 3
        or tokens[0] not in _REF_ADDRESSABLE_SECTIONS
        or tokens[1] != "actions"
        or not tokens[2].startswith(_REF_TOKEN_PREFIX)
    ):
        return path
    ref = tokens[2][len(_REF_TOKEN_PREFIX) :]
    refs = _ref_addressed_action_refs(document, tokens[0])
    if ref in refs:
        index = refs.index(ref)
        return _encode_json_pointer([tokens[0], tokens[1], str(index), *tokens[3:]])
    if append_if_missing and len(tokens) == 3:
        return _encode_json_pointer([tokens[0], tokens[1], "-"])
    raise ToolError(
        f"Unknown action ref {ref!r} in patch path {path!r}. "
        f"Known refs under /{tokens[0]}/actions: {refs}"
    )


def _validate_appended_ref(path: str, resolved_path: str, value: JsonValue) -> None:
    """Ensure a ref-addressed append carries the same ``ref`` it was addressed by."""
    if resolved_path == path or not resolved_path.endswith("/-"):
        return
    ref = _decode_json_pointer(path)[2][len(_REF_TOKEN_PREFIX) :]
    value_ref = value.get("ref") if isinstance(value, dict) else None
    if value_ref != ref:
        raise ToolError(
            f"Patch path {path!r} appends a new action, so the value's 'ref' "
            f"must be {ref!r} (got {value_ref!r})"
        )


def apply_json_patch_operations(
    *,
    document: JsonObject,
    patch_ops: Sequence[JsonPatchOperation],
) -> JsonObject:
    """Apply RFC 6902-style JSON Patch operations to a document.

    ``path`` and ``from`` may address actions by ref
    (``/definition/actions/@<ref>``); each is resolved against the document as
    it stands when that operation runs. See :func:`resolve_action_ref_path`.
    """
    result = copy.deepcopy(document)
    for patch_op in patch_ops:
        match patch_op.op:
            case "add":
                if "value" not in patch_op.model_fields_set:
                    raise ToolError("Patch operation 'add' requires a value")
                path = resolve_action_ref_path(
                    result, patch_op.path, append_if_missing=True
                )
                _validate_appended_ref(patch_op.path, path, patch_op.value)
                _json_pointer_add(result, path, copy.deepcopy(patch_op.value))
            case "remove":
                path = resolve_action_ref_path(result, patch_op.path)
                _json_pointer_remove(result, path)
            case "replace":
                if "value" not in patch_op.model_fields_set:
                    raise ToolError("Patch operation 'replace' requires a value")
                path = resolve_action_ref_path(result, patch_op.path)
                _json_pointer_replace(result, path, copy.deepcopy(patch_op.value))
            case "move":
                if patch_op.from_ is None:
                    raise ToolError("Patch operation 'move' requires a string 'from'")
                from_ = resolve_action_ref_path(result, patch_op.from_)
                moved_value = _json_pointer_remove(result, from_)
                # Resolve the target after the removal so a ref-addressed
                # destination reflects the shifted indexes.
                path = resolve_action_ref_path(result, patch_op.path)
                _json_pointer_add(result, path, moved_value)
            case "copy":
                if patch_op.from_ is None:
                    raise ToolError("Patch operation 'copy' requires a string 'from'")
                from_ = resolve_action_ref_path(result, patch_op.from_)
                copied_value = copy.deepcopy(_json_pointer_get(result, from_))
                path = resolve_action_ref_path(result, patch_op.path)
                _json_pointer_add(result, path, copied_value)
            case "test":
                if "value" not in patch_op.model_fields_set:
                    raise ToolError("Patch operation 'test' requires a value")
                path = resolve_action_ref_path(result, patch_op.path)
                current_value = _json_pointer_get(result, path)
                if current_value != patch_op.value:
                    raise ToolError(
                        f"Patch test operation failed at path {patch_op.path!r}"
                    )
            case _:
                raise ToolError(f"Unsupported patch operation: {patch_op.op!r}")
    return result
