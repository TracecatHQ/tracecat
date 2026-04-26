"""Helpers for Tracecat-internal proxy tool metadata."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from tracecat.logger import logger

PROXY_TOOL_METADATA_KEY = "__tracecat"
PROXY_TOOL_CALL_ID_KEY = "tool_call_id"


def strip_proxy_tool_metadata(args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return tool arguments without Tracecat-internal proxy metadata.

    Args:
        args: Tool arguments that may include Tracecat proxy metadata.

    Returns:
        A shallow-copied dict with the internal metadata removed.
    """
    if not args:
        return {}

    cleaned = dict(args)
    cleaned.pop(PROXY_TOOL_METADATA_KEY, None)
    return cleaned


def extract_proxy_tool_call_id(args: dict[str, Any]) -> str | None:
    """Pop internal Tracecat metadata from proxy tool args and return tool_call_id.

    Args:
        args: Mutable tool arguments sent through the registry proxy.

    Returns:
        The extracted tool call ID if present and well-formed, else ``None``.
    """
    raw_metadata = args.pop(PROXY_TOOL_METADATA_KEY, None)
    if raw_metadata is None:
        return None
    if not isinstance(raw_metadata, dict):
        logger.warning(
            "Ignoring malformed proxy tool metadata",
            metadata_type=type(raw_metadata).__name__,
        )
        return None

    raw_tool_call_id = raw_metadata.get(PROXY_TOOL_CALL_ID_KEY)
    if raw_tool_call_id is None:
        return None
    if not isinstance(raw_tool_call_id, str) or not raw_tool_call_id:
        logger.warning(
            "Ignoring malformed proxy tool call ID",
            tool_call_id_type=type(raw_tool_call_id).__name__,
        )
        return None
    return raw_tool_call_id


def _build_proxy_tool_metadata_schema() -> dict[str, Any]:
    """Build the optional internal metadata schema for registry tools."""
    return {
        "type": "object",
        "properties": {
            PROXY_TOOL_CALL_ID_KEY: {
                "type": "string",
                "description": "Internal Tracecat correlation identifier.",
            }
        },
        "required": [PROXY_TOOL_CALL_ID_KEY],
        "additionalProperties": False,
    }


def build_registry_tool_schema(
    parameters_json_schema: dict[str, Any],
) -> dict[str, Any]:
    """Augment a registry tool schema with optional Tracecat metadata."""
    schema: dict[str, Any] = copy.deepcopy(parameters_json_schema)
    if not schema:
        schema = {"type": "object"}

    schema_type = schema.get("type")
    if schema_type not in (None, "object"):
        raise ValueError(
            f"Registry tools require object-shaped schemas, got type={schema_type!r}"
        )

    raw_properties = schema.get("properties")
    if raw_properties is None:
        properties: dict[str, Any] = {}
        schema["properties"] = properties
    elif isinstance(raw_properties, dict):
        properties = raw_properties
    else:
        raise ValueError("Registry tool schema properties must be an object")

    schema.setdefault("type", "object")
    properties[PROXY_TOOL_METADATA_KEY] = _build_proxy_tool_metadata_schema()
    return schema


def sanitize_message_tool_inputs(message: dict[str, Any]) -> dict[str, Any]:
    """Remove proxy-only metadata from tool inputs in a persisted message payload.

    Args:
        message: Raw persisted message payload.

    Returns:
        A deep-copied message with internal proxy metadata stripped from any
        tool-call input payloads.
    """
    sanitized = copy.deepcopy(message)

    if isinstance(parts := sanitized.get("parts"), list):
        for part in parts:
            if (
                isinstance(part, dict)
                and part.get("part_kind") in {"tool-call", "builtin-tool-call"}
                and isinstance(raw_args := part.get("args"), Mapping)
            ):
                part["args"] = strip_proxy_tool_metadata(raw_args)

    if isinstance(content := sanitized.get("content"), list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and isinstance(raw_input := block.get("input"), Mapping)
            ):
                block["input"] = strip_proxy_tool_metadata(raw_input)

    return sanitized
