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


def is_numeric_index_token(token: str) -> bool:
    """Return True if a token is a well-formed JSON Pointer array index."""
    return token == "0" or (
        token.isascii() and token.isdigit() and not token.startswith("0")
    )


def _is_actions_array_prefix(tokens: list[str], depth: int) -> bool:
    """True when tokens[:depth] addresses the /definition/actions array."""
    return depth == 2 and tokens[0] == "definition" and tokens[1] == "actions"


def _json_pointer_array_index(
    token: str,
    *,
    length: int,
    allow_end: bool = False,
    array: JsonArray | None = None,
    reject_numeric_index: bool = False,
) -> int:
    """Convert an array JSON Pointer token to an integer index.

    In addition to RFC 6902 numeric indexes (and ``-`` for append when
    ``allow_end``), a token may be an **action ref** (a slug) that addresses an
    element of the array by its ``ref`` field. Refs may be all-digits, so a
    ref-match takes precedence over numeric-index interpretation. Ref resolution
    requires the array elements (``array``) and happens against the array being
    patched at apply time.

    When ``reject_numeric_index`` is set (the ``/definition/actions`` array),
    an all-digit token that does not match an existing ref is rejected: actions
    must be addressed by ref, never by positional index.
    """
    if allow_end and token == "-":
        return length
    # Ref-match wins over index interpretation.
    if array is not None:
        for idx, element in enumerate(array):
            if isinstance(element, dict) and element.get("ref") == token:
                return idx
    if reject_numeric_index and token.isascii() and token.isdigit():
        raise ToolError(
            f"Address actions by ref, not by numeric index: {token!r}. The "
            "/definition/actions array does not accept positional indexes; use "
            "the action's ref (or '-' to append)."
        )
    if is_numeric_index_token(token):
        index = int(token)
        max_index = length if allow_end else length - 1
        if index < 0 or index > max_index:
            raise ToolError(f"Patch array index out of range: {token!r}")
        return index
    # Unmatched all-digit non-canonical tokens (e.g. "01") are malformed indexes.
    if token.isascii() and token.isdigit():
        raise ToolError(f"Invalid array index in patch path: {token!r}")
    if array is not None:
        raise ToolError(f"Patch path ref not found in array: {token!r}")
    raise ToolError(f"Invalid array index in patch path: {token!r}")


def _json_pointer_get(document: JsonValue, path: str) -> JsonValue:
    """Resolve a JSON Pointer path against a document."""
    current = document
    tokens = _decode_json_pointer(path)
    for depth, token in enumerate(tokens):
        if isinstance(current, dict):
            if token not in current:
                raise ToolError(f"Patch path not found: {path!r}")
            current = current[token]
        elif isinstance(current, list):
            ref_lookup = _is_actions_array_prefix(tokens, depth)
            index = _json_pointer_array_index(
                token,
                length=len(current),
                array=current if ref_lookup else None,
                reject_numeric_index=ref_lookup,
            )
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
    for depth, token in enumerate(tokens[:-1]):
        if isinstance(parent, dict):
            if token not in parent:
                raise ToolError(f"Patch path not found: {path!r}")
            parent = parent[token]
        elif isinstance(parent, list):
            ref_lookup = _is_actions_array_prefix(tokens, depth)
            index = _json_pointer_array_index(
                token,
                length=len(parent),
                array=parent if ref_lookup else None,
                reject_numeric_index=ref_lookup,
            )
            parent = parent[index]
        else:
            raise ToolError(f"Patch path not found: {path!r}")
    if not isinstance(parent, (dict, list)):
        raise ToolError(f"Patch path not found: {path!r}")
    return parent, tokens[-1]


def _final_token_targets_actions_array(path: str) -> bool:
    """True when the final token of ``path`` indexes /definition/actions."""
    return _decode_json_pointer(path)[:-1] == ["definition", "actions"]


def _json_pointer_add(document: JsonValue, path: str, value: JsonValue) -> None:
    """Apply a JSON Patch add operation."""
    parent, token = _json_pointer_get_parent(document, path)
    if isinstance(parent, dict):
        parent[token] = value
        return
    if isinstance(parent, list):
        ref_lookup = _final_token_targets_actions_array(path)
        index = _json_pointer_array_index(
            token,
            length=len(parent),
            allow_end=True,
            array=parent if ref_lookup else None,
            reject_numeric_index=ref_lookup,
        )
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
        ref_lookup = _final_token_targets_actions_array(path)
        index = _json_pointer_array_index(
            token,
            length=len(parent),
            array=parent if ref_lookup else None,
            reject_numeric_index=ref_lookup,
        )
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
        ref_lookup = _final_token_targets_actions_array(path)
        index = _json_pointer_array_index(
            token,
            length=len(parent),
            array=parent if ref_lookup else None,
            reject_numeric_index=ref_lookup,
        )
        parent[index] = value
        return
    raise ToolError(f"Patch path not found: {path!r}")


def apply_json_patch_operations(
    *,
    document: JsonObject,
    patch_ops: Sequence[JsonPatchOperation],
) -> JsonObject:
    """Apply RFC 6902-style JSON Patch operations to a document."""
    result = copy.deepcopy(document)
    for patch_op in patch_ops:
        match patch_op.op:
            case "add":
                if "value" not in patch_op.model_fields_set:
                    raise ToolError("Patch operation 'add' requires a value")
                _json_pointer_add(result, patch_op.path, copy.deepcopy(patch_op.value))
            case "remove":
                _json_pointer_remove(result, patch_op.path)
            case "replace":
                if "value" not in patch_op.model_fields_set:
                    raise ToolError("Patch operation 'replace' requires a value")
                _json_pointer_replace(
                    result, patch_op.path, copy.deepcopy(patch_op.value)
                )
            case "move":
                if patch_op.from_ is None:
                    raise ToolError("Patch operation 'move' requires a string 'from'")
                moved_value = _json_pointer_remove(result, patch_op.from_)
                _json_pointer_add(result, patch_op.path, moved_value)
            case "copy":
                if patch_op.from_ is None:
                    raise ToolError("Patch operation 'copy' requires a string 'from'")
                copied_value = copy.deepcopy(_json_pointer_get(result, patch_op.from_))
                _json_pointer_add(result, patch_op.path, copied_value)
            case "test":
                if "value" not in patch_op.model_fields_set:
                    raise ToolError("Patch operation 'test' requires a value")
                current_value = _json_pointer_get(result, patch_op.path)
                if current_value != patch_op.value:
                    raise ToolError(
                        f"Patch test operation failed at path {patch_op.path!r}"
                    )
            case _:
                raise ToolError(f"Unsupported patch operation: {patch_op.op!r}")
    return result
