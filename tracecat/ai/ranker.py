"""LLM-based ranking of items using pydantic AI.

This module provides functionality to rank a list of items based on natural language
criteria using large language models. Inspired by BishopFox's raink tool but simplified
for integration with Tracecat's agent infrastructure.
"""

import asyncio
import json
import random
from typing import Any, TypedDict

from tracecat.agent.factory import build_agent
from tracecat.agent.models import AgentConfig
from tracecat.agent.runtime import run_agent_sync
from tracecat.logger import logger

# Global constraint to defend against resource consumption attacks
MAX_ITEMS: int = 100


class RankableItem(TypedDict):
    id: str | int
    text: str


async def rank_items(
    items: list[dict[str, Any]],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
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
        model_name: LLM model to use
        model_provider: LLM provider
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

    if len(items) > MAX_ITEMS:
        raise ValueError(f"Too many items to rank: received {len(items)}.")

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
        if isinstance(output_text, list):
            ranked_ids = output_text
        else:
            # Clean up the output - LLMs sometimes wrap responses in markdown code blocks
            cleaned = str(output_text).strip()
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


# Helper functions for pairwise ranking algorithm


def _shuffle_items(items: list[RankableItem]) -> list[RankableItem]:
    """Shuffle items randomly to reduce positional bias."""
    shuffled = items.copy()
    random.shuffle(shuffled)
    return shuffled


def _create_batches(
    items: list[RankableItem], batch_size: int
) -> list[list[RankableItem]]:
    """Divide items into batches of specified size."""
    batches: list[list[RankableItem]] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batches.append(batch)
    return batches


def _average_scores(scores: dict[str | int, list[float]]) -> dict[str | int, float]:
    """Average positional scores across multiple passes.

    Args:
        scores: Dict mapping item IDs to lists of positional scores

    Returns:
        Dict mapping item IDs to average scores
    """
    return {
        item_id: sum(score_list) / len(score_list)
        for item_id, score_list in scores.items()
    }


async def _rank_batch(
    batch: list[RankableItem],
    criteria_prompt: str,
    id_field: str,
    agent: Any,
    max_requests: int,
) -> list[str | int]:
    """Rank a single batch of items using LLM.

    Args:
        batch: List of items to rank
        criteria_prompt: Natural language criteria for ranking
        id_field: Field name containing the item ID
        agent: Pre-built agent instance
        max_requests: Maximum number of LLM requests

    Returns:
        List of item IDs in ranked order (best to worst)

    Raises:
        ValueError: If LLM response cannot be parsed or is invalid
    """
    # Format batch items using only 'text' field
    item_descriptions = []
    for item in batch:
        item_id = item[id_field]
        text_content = item.get("text", "")
        if not text_content:
            # Fallback: if no 'text' field, use JSON dump
            text_content = json.dumps(item, indent=2)
        item_descriptions.append(f"id: `{item_id}`\ntext: {text_content}")

    items_text = "\n\n".join(item_descriptions)

    # Build ranking prompt
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

    # Run the agent
    result = await run_agent_sync(agent, user_prompt, max_requests=max_requests)

    # Extract and parse output
    result_dict = result.model_dump()
    output_text = (
        result_dict.get("output") or result_dict.get("data") or str(result_dict)
    )

    # Parse as JSON, allowing pre-parsed list outputs
    try:
        if isinstance(output_text, list):
            ranked_ids = output_text
        else:
            cleaned = str(output_text).strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            ranked_ids = json.loads(cleaned)

        if not isinstance(ranked_ids, list):
            raise ValueError(f"LLM did not return a list. Response: {output_text}")
        return ranked_ids
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM response is not valid JSON. Response: {output_text}"
        ) from e


async def rank_items_pairwise(
    items: list[RankableItem],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    id_field: str = "id",
    batch_size: int = 10,
    num_passes: int = 10,
    refinement_ratio: float = 0.5,
    model_settings: dict[str, Any] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
) -> list[str | int]:
    """Rank items using multi-pass pairwise ranking with progressive refinement.

    This implements the BishopFox raink algorithm:
    1. Multiple passes of shuffle→batch→rank to reduce positional bias
    2. Average positional scores across all passes for robust ranking
    3. Progressive refinement: recursively re-rank top candidates
    4. Parallel LLM calls for batch processing

    Args:
        items: List of items to rank. Each item must be a RankableItem.
        criteria_prompt: Natural language criteria for ranking (e.g., "by severity")
        model_name: LLM model to use
        model_provider: LLM provider
        id_field: Field name containing the item ID (default: "id")
        batch_size: Number of items per batch (default: 10)
        num_passes: Number of shuffle-batch-rank iterations (default: 10)
        refinement_ratio: Portion of top items to recursively refine (default: 0.5)
        model_settings: Optional model settings dict (temperature, etc.)
        max_requests: Maximum number of LLM requests per batch (default: 5)
        retries: Number of retries on failure (default: 3)
        base_url: Optional base URL for custom providers

    Returns:
        List of item IDs in ranked order (best to worst according to criteria)

    Raises:
        ValueError: If items are empty, missing ID field, or invalid parameters

    Examples:
        >>> items = [
        ...     {"id": "A", "text": "Critical security vulnerability in authentication"},
        ...     {"id": "B", "text": "Minor UI bug in settings page"},
        ...     {"id": "C", "text": "Data breach incident affecting 1000 users"},
        ...     {"id": "D", "text": "Performance issue causing 2s page load"}
        ... ]
        >>> ranked = await rank_items_pairwise(
        ...     items, "by severity for security team",
        ...     batch_size=10, num_passes=10, refinement_ratio=0.5
        ... )
        >>> ranked
        ["C", "A", "D", "B"]
    """
    if not items:
        return []

    if len(items) > MAX_ITEMS:
        raise ValueError(f"Too many items to rank: received {len(items)}.")

    # Validate items have ID field
    for idx, item in enumerate(items):
        if id_field not in item:
            raise ValueError(
                f"Item at index {idx} missing required field '{id_field}': {item}"
            )

    # Short-circuit for single item - no ranking needed
    if len(items) == 1:
        return [items[0][id_field]]

    # Build agent once for reuse
    agent = await build_agent(
        AgentConfig(
            model_name=model_name,
            model_provider=model_provider,
            model_settings=model_settings,
            retries=retries,
            base_url=base_url,
        )
    )

    # Perform multi-pass ranking with refinement
    ranked_ids = await _multi_pass_rank(
        items=items,
        criteria_prompt=criteria_prompt,
        id_field=id_field,
        agent=agent,
        batch_size=batch_size,
        num_passes=num_passes,
        refinement_ratio=refinement_ratio,
        max_requests=max_requests,
        depth=0,
    )

    return ranked_ids


async def _multi_pass_rank(
    items: list[RankableItem],
    criteria_prompt: str,
    id_field: str,
    agent: Any,
    batch_size: int,
    num_passes: int,
    refinement_ratio: float,
    max_requests: int,
    depth: int = 0,
) -> list[str | int]:
    """Perform multi-pass ranking with optional recursive refinement.

    Args:
        items: List of items to rank
        criteria_prompt: Natural language criteria
        id_field: Field name for item ID
        agent: Pre-built agent instance
        batch_size: Items per batch
        num_passes: Number of shuffle-rank passes
        refinement_ratio: Top portion to refine (0-1)
        max_requests: Max LLM requests per batch
        depth: Current recursion depth (for score weighting)

    Returns:
        List of item IDs in ranked order
    """
    if len(items) <= 1:
        # Base case: single item
        return [items[0][id_field]] if items else []

    logger.info(
        "Multi-pass ranking",
        depth=depth,
        num_items=len(items),
        num_passes=num_passes,
        batch_size=batch_size,
    )

    # Phase 1: Multi-pass batch ranking
    # Collect positional scores across multiple passes
    scores: dict[str | int, list[float]] = {}

    for pass_num in range(num_passes):
        # Shuffle items to reduce positional bias
        shuffled_items = _shuffle_items(items)

        # Create batches
        batches = _create_batches(shuffled_items, batch_size)

        # Rank all batches in parallel
        batch_tasks = [
            _rank_batch(
                batch=batch,
                criteria_prompt=criteria_prompt,
                id_field=id_field,
                agent=agent,
                max_requests=max_requests,
            )
            for batch in batches
        ]

        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

        # Process results and collect scores
        for batch_idx, result in enumerate(batch_results):
            if isinstance(result, Exception):
                logger.warning(
                    "Batch ranking failed",
                    pass_num=pass_num,
                    batch_idx=batch_idx,
                    error=str(result),
                )
                # Skip failed batches
                continue

            # Type assertion: after Exception check, result must be list[str | int]
            if not isinstance(result, list):
                # This should never happen but satisfies type checker
                logger.warning(
                    "Unexpected result type",
                    pass_num=pass_num,
                    batch_idx=batch_idx,
                    result_type=type(result).__name__,
                )
                continue

            ranked_ids: list[str | int] = result
            batch = batches[batch_idx]

            # Assign positional scores (0-based index within batch)
            # Lower position = better rank = lower score
            for position, item_id in enumerate(ranked_ids):
                if item_id not in scores:
                    scores[item_id] = []
                scores[item_id].append(float(position))

            # Handle items that weren't returned by LLM
            batch_ids: set[str | int] = {item[id_field] for item in batch}
            returned_ids: set[str | int] = set(ranked_ids)
            missing_ids = batch_ids - returned_ids

            if missing_ids:
                logger.warning(
                    "LLM did not return all items",
                    pass_num=pass_num,
                    batch_idx=batch_idx,
                    missing_count=len(missing_ids),
                )
                # Assign worst position to missing items
                worst_position = len(ranked_ids)
                for item_id in missing_ids:
                    if item_id not in scores:
                        scores[item_id] = []
                    scores[item_id].append(float(worst_position))

    # Average scores across passes
    avg_scores = _average_scores(scores)

    # Sort items by average score (lower = better)
    sorted_ids = sorted(avg_scores.keys(), key=lambda x: avg_scores[x])

    # Phase 2: Refinement (recursive re-ranking of top portion)
    if refinement_ratio > 0 and len(sorted_ids) > 1:
        # Select top portion for refinement
        refinement_count = max(1, int(len(sorted_ids) * refinement_ratio))

        if refinement_count < len(sorted_ids):
            top_ids = sorted_ids[:refinement_count]
            bottom_ids = sorted_ids[refinement_count:]

            # Filter items for refinement
            top_items = [item for item in items if item[id_field] in top_ids]

            # Recursively refine the top portion
            # Reduce passes and refinement ratio for deeper levels
            refined_top_ids = await _multi_pass_rank(
                items=top_items,
                criteria_prompt=criteria_prompt,
                id_field=id_field,
                agent=agent,
                batch_size=batch_size,
                num_passes=max(1, num_passes // 2),
                refinement_ratio=refinement_ratio,
                max_requests=max_requests,
                depth=depth + 1,
            )

            # Combine refined top with bottom
            return refined_top_ids + bottom_ids

    return sorted_ids
