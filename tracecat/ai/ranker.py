"""LLM-based ranking of items using pydantic AI.

This module provides functionality to rank a list of items based on natural language
criteria using large language models. Inspired by BishopFox's raink tool but simplified
for integration with Tracecat's agent infrastructure.
"""

import json
from typing import Any

from tracecat.agent.factory import build_agent
from tracecat.agent.models import AgentConfig
from tracecat.agent.runtime import run_agent_sync


async def rank_items(
    items: list[dict[str, Any]],
    criteria_prompt: str,
    model_name: str = "gpt-4o-mini",
    model_provider: str = "openai",
    id_field: str = "id",
    model_settings: dict[str, Any] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
) -> list[Any]:
    """Rank items using an LLM based on natural language criteria.

    Given a list of items and a criteria prompt, returns the item IDs ranked
    according to the criteria. Uses prompting (not structured outputs) to
    get the LLM to return a JSON list of IDs in ranked order.

    Args:
        items: List of items to rank, each must contain the id_field
        criteria_prompt: Natural language criteria for ranking (e.g., "by severity", "most relevant to security")
        model_name: LLM model to use (default: gpt-4o-mini)
        model_provider: LLM provider (default: openai)
        id_field: Field name containing the item ID (default: "id")
        model_settings: Optional model settings dict (temperature, etc.)
        max_requests: Maximum number of LLM requests (default: 5)
        retries: Number of retries on failure (default: 3)
        base_url: Optional base URL for custom providers

    Returns:
        List of item IDs in ranked order (best to worst according to criteria)

    Raises:
        ValueError: If items are empty, missing ID field, LLM response cannot be
                   parsed as a JSON list, or if the LLM requests clarification
                   instead of ranking

    Examples:
        >>> items = [
        ...     {"id": "A", "text": "Critical security vulnerability"},
        ...     {"id": "B", "text": "Minor UI bug"},
        ...     {"id": "C", "text": "Data breach incident"}
        ... ]
        >>> ranked = await rank_items(items, "by severity for security team")
        >>> ranked
        ["C", "A", "B"]

        >>> alerts = [
        ...     {"id": "alert-1", "severity": "low", "type": "info"},
        ...     {"id": "alert-2", "severity": "critical", "type": "security"},
        ...     {"id": "alert-3", "severity": "medium", "type": "performance"}
        ... ]
        >>> ranked = await rank_items(alerts, "prioritize security issues")
        >>> ranked
        ["alert-2", "alert-3", "alert-1"]
    """
    if not items:
        return []

    # Validate and extract item descriptions
    item_descriptions = []
    for idx, item in enumerate(items):
        if id_field not in item:
            raise ValueError(
                f"Item at index {idx} missing required field '{id_field}': {item}"
            )
        item_id = item[id_field]
        # Format each item with its ID and full content
        item_descriptions.append(
            f"id: `{item_id}`\nvalue:\n```\n{json.dumps(item, indent=2)}\n```"
        )

    items_text = "\n\n".join(item_descriptions)

    # Build the ranking prompt with clear instructions and examples
    user_prompt = f"""Rank the following items based on this criteria: {criteria_prompt}

Items to rank:
{items_text}

INSTRUCTIONS:
- Analyze each item against the criteria
- Return ONLY a JSON array of the IDs in ranked order (best to worst)
- Do not include explanations, reasoning, or any additional text
- Always respond with IDs, never with actual item values

Examples of correct output format:
["ID1", "ID3", "ID2"]
["alert-critical", "alert-high", "alert-medium"]
[123, 456, 789]

Your response (JSON array only):"""

    # Build and run the agent
    agent = await build_agent(
        AgentConfig(
            model_name=model_name,
            model_provider=model_provider,
            model_settings=model_settings,
            retries=retries,
            base_url=base_url,
        )
    )

    result = await run_agent_sync(agent, user_prompt, max_requests=max_requests)

    # Extract the output from the result
    result_dict = result.model_dump()

    # Try different possible fields where the output might be stored
    output_text = (
        result_dict.get("output") or result_dict.get("data") or str(result_dict)
    )

    # Parse and validate the response
    try:
        # Clean up the output - LLMs sometimes wrap responses in markdown code blocks
        cleaned = output_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # Parse as JSON
        ranked_ids = json.loads(cleaned)

        if not isinstance(ranked_ids, list):
            raise ValueError(
                f"LLM did not return a list. It may be requesting clarification. "
                f"Response: {output_text}"
            )

        return ranked_ids

    except json.JSONDecodeError as e:
        # LLM likely asking for clarification or provided non-JSON response
        raise ValueError(
            f"LLM response is not valid JSON. The criteria may need clarification. "
            f"Response: {output_text}"
        ) from e
