"""Anthropic compatibility helpers shared across provider adapters.

This module owns the small amount of cross-provider request shaping that is
still shared after normalization:

- tool schema conversion between Anthropic and OpenAI wire shapes
- canonical Anthropic tool choice parsing
- structured-output schema unwrapping
- deterministic tool name and tool call ID truncation
"""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

OPENAI_MAX_TOOL_CALL_ID_LENGTH = 40
OPENAI_MAX_TOOL_NAME_LENGTH = 64
_TOOL_ID_HASH_LENGTH = 8
_TOOL_ID_PREFIX_LENGTH = OPENAI_MAX_TOOL_CALL_ID_LENGTH - _TOOL_ID_HASH_LENGTH - 1
_TOOL_NAME_HASH_LENGTH = 8
_TOOL_NAME_PREFIX_LENGTH = OPENAI_MAX_TOOL_NAME_LENGTH - _TOOL_NAME_HASH_LENGTH - 1
_EMPTY_TOOL_INPUT_SCHEMA = {"type": "object", "properties": {}}


def truncate_tool_call_id(tool_id: str) -> str:
    """Deterministically shorten tool call IDs to OpenAI's length limit."""
    if len(tool_id) <= OPENAI_MAX_TOOL_CALL_ID_LENGTH:
        return tool_id
    id_hash = hashlib.sha256(tool_id.encode()).hexdigest()[:_TOOL_ID_HASH_LENGTH]
    return f"{tool_id[:_TOOL_ID_PREFIX_LENGTH]}_{id_hash}"


def truncate_tool_name(tool_name: str) -> str:
    """Deterministically shorten tool names to OpenAI's length limit."""
    if len(tool_name) <= OPENAI_MAX_TOOL_NAME_LENGTH:
        return tool_name
    name_hash = hashlib.sha256(tool_name.encode()).hexdigest()[:_TOOL_NAME_HASH_LENGTH]
    return f"{tool_name[:_TOOL_NAME_PREFIX_LENGTH]}_{name_hash}"


def create_tool_name_mapping(
    tools: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, str]:
    """Map truncated tool names back to the original tool names."""
    mapping: dict[str, str] = {}
    for tool in tools:
        if (name := _tool_name(tool)) is None:
            continue
        truncated_name = truncate_tool_name(name)
        if truncated_name != name:
            mapping[truncated_name] = name
    return mapping


def restore_tool_name(name: str | None, mapping: dict[str, str]) -> str | None:
    """Restore a previously truncated tool name."""
    if name is None:
        return None
    return mapping.get(name, name)


def anthropic_tool_definition(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the canonical Anthropic tool definition from a normalized tool."""
    match tool:
        case {"name": str(name), **tool_spec_fields}:
            definition: dict[str, Any] = {
                "name": name,
                "input_schema": _tool_input_schema(
                    tool_spec_fields.get("input_schema")
                ),
            }
            if description := tool_spec_fields.get("description"):
                definition["description"] = str(description)
            return definition
        case _:
            return None


def anthropic_tool_to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Render an Anthropic-style tool definition into OpenAI's function shape."""
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        return dict(tool)

    if (definition := anthropic_tool_definition(tool)) is None:
        return dict(tool)

    function: dict[str, Any] = {"name": definition["name"]}
    if description := definition.get("description"):
        function["description"] = description
    function["parameters"] = definition["input_schema"]
    return {"type": "function", "function": function}


def tool_definition_to_anthropic(tool: dict[str, Any]) -> dict[str, Any]:
    """Render an OpenAI function definition into Anthropic's tool shape."""
    if definition := anthropic_tool_definition(tool):
        return definition

    function = tool.get("function")
    if not isinstance(function, dict):
        return dict(tool)

    anthropic_tool: dict[str, Any] = {"name": str(function.get("name", ""))}
    if "description" in function:
        anthropic_tool["description"] = function["description"]
    anthropic_tool["input_schema"] = _tool_input_schema(function.get("parameters"))
    return anthropic_tool


def anthropic_tools_to_openai_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Translate Anthropic tools into OpenAI tool definitions with name mapping."""
    mapping = create_tool_name_mapping(tools)
    translated: list[dict[str, Any]] = []
    for tool in tools:
        openai_tool = anthropic_tool_to_openai_tool(tool)
        function = openai_tool.get("function")
        if isinstance(function, dict):
            function["name"] = truncate_tool_name(str(function.get("name", "")))
        translated.append(openai_tool)
    return translated, mapping


def tool_result_content_to_openai(content: Any) -> str | list[Any]:
    """Render Anthropic tool result content into an OpenAI-compatible shape."""
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text", ""))
        try:
            return orjson.dumps(content).decode("utf-8")
        except TypeError:
            return str(content)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if len(content) == 1:
            item = content[0]
            if isinstance(item, str):
                return item
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text", ""))
        return [
            {"type": "text", "text": item} if isinstance(item, str) else item
            for item in content
        ]
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="ignore")
    try:
        return orjson.dumps(content).decode("utf-8")
    except TypeError:
        return str(content)


def tool_result_to_anthropic_block(
    *,
    tool_use_id: str | None,
    content: Any,
    is_error: bool,
) -> dict[str, Any]:
    """Render a tool result into Anthropic's `tool_result` block shape."""
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id or "",
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block


def tool_choice_to_openai(tool_choice: Any) -> Any:
    """Render an Anthropic tool choice into an OpenAI-compatible value."""
    if isinstance(tool_choice, str):
        if tool_choice == "any":
            return "required"
        return tool_choice
    if not isinstance(tool_choice, dict):
        return tool_choice

    match tool_choice.get("type"):
        case "auto":
            return "auto"
        case "any":
            return "required"
        case "tool":
            return {
                "type": "function",
                "function": {"name": str(tool_choice.get("name", ""))},
            }
        case "function":
            return dict(tool_choice)
        case _:
            if isinstance(function := tool_choice.get("function"), dict):
                return {
                    "type": "function",
                    "function": {"name": str(function.get("name", ""))},
                }
            if "name" in tool_choice:
                return {
                    "type": "function",
                    "function": {"name": str(tool_choice.get("name", ""))},
                }
            return dict(tool_choice)


def anthropic_tool_choice(tool_choice: Any) -> dict[str, str] | None:
    """Extract the canonical Anthropic tool choice shape."""
    if isinstance(tool_choice, str):
        if tool_choice in {"auto", "none"}:
            return {"type": tool_choice}
        if tool_choice == "required":
            return {"type": "any"}
        return None
    if not isinstance(tool_choice, dict):
        return None

    match tool_choice.get("type"):
        case "tool":
            if isinstance(name := tool_choice.get("name"), str):
                return {"type": "tool", "name": name}
            return None
        case "auto" | "any" | "none" as choice_type:
            return {"type": choice_type}
        case "function":
            if isinstance(function := tool_choice.get("function"), dict) and isinstance(
                name := function.get("name"),
                str,
            ):
                return {"type": "tool", "name": name}
            return None
        case _:
            if isinstance(function := tool_choice.get("function"), dict) and isinstance(
                name := function.get("name"),
                str,
            ):
                return {"type": "tool", "name": name}
            if isinstance(name := tool_choice.get("name"), str):
                return {"type": "tool", "name": name}
            return None


def tool_choice_to_anthropic(tool_choice: Any) -> Any:
    """Render an OpenAI tool choice into an Anthropic-compatible value."""
    if anthropic_choice := anthropic_tool_choice(tool_choice):
        return anthropic_choice
    return tool_choice


def _tool_input_schema(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else dict(_EMPTY_TOOL_INPUT_SCHEMA)


def _tool_name(tool: dict[str, Any]) -> str | None:
    match tool:
        case {"name": str(name)}:
            return name
        case {"type": "function", "function": {"name": str(name)}}:
            return name
        case {"function": {"name": str(name)}}:
            return name
        case _:
            return None
