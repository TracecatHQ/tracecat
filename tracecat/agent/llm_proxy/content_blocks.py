"""Claude-specific content block translation and thinking block sanitization.

This module handles:

- Rewriting server-managed content block types (server_tool_use,
  web_search_tool_result, etc.) into standard tool_use / tool_result blocks
  that all providers understand.
- Sanitizing thinking blocks for Anthropic API compatibility.
- Stashing translated content blocks into message metadata for provider replay.
"""

from __future__ import annotations

from typing import Any

# Metadata key used to stash Anthropic content blocks on normalized messages.
ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY = "_anthropic_content_blocks"

# Block types that map to tool_use (assistant invoking a tool)
_SERVER_TOOL_USE_TYPES = frozenset(
    {
        "server_tool_use",
        "mcp_tool_use",
        "container_tool_use",
    }
)

# Block types that map to tool_result (result returned from a tool)
_SERVER_TOOL_RESULT_TYPES = frozenset(
    {
        "web_search_tool_result",
        "code_execution_tool_result",
        "mcp_tool_result",
        "container_tool_result",
    }
)


def _format_web_search_result_content(content: Any) -> str:
    """Extract readable text from a web_search_tool_result content field."""
    if isinstance(content, dict):
        # Error case: {"type": "web_search_tool_result_error", "error_code": ...}
        error_code = content.get("error_code", "unknown_error")
        return f"[Web search error: {error_code}]"
    if isinstance(content, list):
        parts: list[str] = []
        for result in content:
            if not isinstance(result, dict):
                continue
            title = result.get("title", "")
            url = result.get("url", "")
            # encrypted_content is opaque; include title + URL which are readable
            parts.append(f"- {title}\n  {url}")
        return "\n".join(parts) if parts else "[No search results]"
    return str(content) if content else "[No search results]"


def _format_code_execution_result_content(content: Any) -> str:
    """Extract readable text from a code_execution_tool_result content field."""
    if not isinstance(content, dict):
        return str(content) if content else "[No execution output]"
    content_type = content.get("type", "")
    if content_type == "code_execution_tool_result_error":
        error_code = content.get("error_code", "unknown_error")
        return f"[Code execution error: {error_code}]"
    # code_execution_result: has stdout, stderr, return_code
    stdout = content.get("stdout", "")
    stderr = content.get("stderr", "")
    return_code = content.get("return_code")
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr] {stderr}")
    if return_code is not None and return_code != 0:
        parts.append(f"[exit code: {return_code}]")
    return "\n".join(parts) if parts else "[No execution output]"


def _format_mcp_tool_result_content(content: Any) -> Any:
    """Normalise mcp_tool_result content (str or list of text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts) if parts else content
    return content


def format_server_tool_result_content(block_type: str, content: Any) -> Any:
    """Format the content of a server tool result block into plain text/data."""
    match block_type:
        case "web_search_tool_result":
            return _format_web_search_result_content(content)
        case "code_execution_tool_result":
            return _format_code_execution_result_content(content)
        case "mcp_tool_result":
            return _format_mcp_tool_result_content(content)
        case _:
            # container_tool_result or unknown — pass through
            return content


def translate_anthropic_content_block(item: dict[str, Any]) -> dict[str, Any] | None:
    """Rewrite a Claude-specific block into a standard block, or return as-is.

    Returns None for blocks that should be dropped entirely (tool_reference).
    """
    block_type = item.get("type")

    if block_type == "tool_reference":
        # Content-free pointer to a tool name — not needed by any provider.
        return None

    if block_type in _SERVER_TOOL_USE_TYPES:
        # Rewrite as a standard tool_use block.
        return {
            "type": "tool_use",
            "id": str(item.get("id", "")),
            "name": str(item.get("name", "")),
            "input": item.get("input", {}),
        }

    if block_type in _SERVER_TOOL_RESULT_TYPES:
        # Rewrite as a standard tool_result block.
        content = format_server_tool_result_content(block_type, item.get("content"))
        return {
            "type": "tool_result",
            "tool_use_id": str(item.get("tool_use_id", "")),
            "content": content,
            "is_error": bool(item.get("is_error", False)),
        }

    # Standard block type — keep as-is.
    return {str(key): value for key, value in item.items()}


def metadata_with_anthropic_content_blocks(
    metadata: dict[str, Any],
    content: list[Any],
) -> dict[str, Any]:
    """Attach Anthropic content blocks for provider replay.

    Claude-specific block types (server_tool_use, web_search_tool_result, etc.)
    are rewritten into standard tool_use/tool_result blocks so that downstream
    providers (OpenAI Responses API, Bedrock, Google) can consume them without
    encountering unknown types.  tool_reference blocks are dropped entirely
    (they are content-free pointers).
    """
    translated: list[Any] = []
    for item in content:
        if not isinstance(item, dict):
            translated.append(item)
            continue
        block = translate_anthropic_content_block(item)
        if block is not None:
            translated.append(block)
    return {
        **metadata,
        ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY: translated,
    }


def sanitize_thinking_block(block: dict[str, Any]) -> dict[str, Any]:
    """Copy a content block, cleaning thinking blocks for Anthropic compatibility.

    Thinking blocks with a valid Anthropic signature are kept as-is (with
    unknown fields stripped). Thinking blocks from other providers (no
    signature, or non-Anthropic signature) are converted to text blocks so
    the reasoning content is preserved without triggering Anthropic errors.
    Non-thinking blocks are copied as-is.
    """
    result = {str(key): value for key, value in block.items()}
    if result.get("type") != "thinking":
        return result
    # Valid Anthropic signature — keep as thinking block, strip extras
    signature = result.get("signature")
    if isinstance(signature, str) and signature.startswith("ErC"):
        for key in ("id", "summary"):
            result.pop(key, None)
        return result
    # No valid signature — extract text and convert to text block
    thinking = result.get("thinking", "")
    if not thinking and isinstance(summary := result.get("summary"), list):
        thinking = "".join(
            str(part.get("text", "")) for part in summary if isinstance(part, dict)
        )
    return {"type": "text", "text": str(thinking) if thinking else ""}


def sanitize_thinking_blocks(payload: dict[str, Any]) -> None:
    """Clean thinking blocks in an Anthropic payload in-place.

    Strips non-Anthropic fields (id, summary, foreign signatures) from
    thinking blocks that originated from other providers like OpenAI.
    Populates thinking from summary if empty.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        message["content"] = [
            sanitize_thinking_block(item) if isinstance(item, dict) else item
            for item in content
        ]


def coerce_anthropic_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Coerce content into a list of Anthropic-style content blocks."""
    if content is None:
        return []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                blocks.append(sanitize_thinking_block(item))
            else:
                blocks.append({"type": "text", "text": str(item)})
        return blocks
    if isinstance(content, dict):
        return [sanitize_thinking_block(content)]
    return [{"type": "text", "text": str(content)}]
