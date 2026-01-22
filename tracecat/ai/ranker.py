"""LLM-based ranking of items using pydantic AI.

This module provides functionality to rank a list of items based on natural language
criteria using large language models. Inspired by BishopFox's raink tool but simplified
for integration with Tracecat's agent infrastructure.
"""

import asyncio
import random
import textwrap
from typing import Any, TypedDict

from pydantic_ai import Agent

from tracecat.agent.factory import build_agent
from tracecat.agent.runtime.pydantic_ai.runtime import run_agent_sync
from tracecat.agent.schemas import AgentOutput
from tracecat.agent.types import AgentConfig
from tracecat.logger import logger

# Global constraint to defend against resource consumption attacks
MAX_ITEMS: int = 100


RANKING_SYSTEM_PROMPT_TEMPLATE = textwrap.dedent("""
You are a ranking assistant. Your task is to rank items based on the given criteria.

Criteria:
{criteria_prompt}

Requirements:
- Rank each item against the criteria from most to least relevant
{length_requirement}
- Return ONLY a JSON array of IDs in ranked order: ["id1", "id2", ...]
- Do not include explanations, reasoning, markdown formatting, or other text
- The response must be valid deserializable JSON array (start and ends with square brackets)
""").strip()


RANKING_USER_PROMPT_TEMPLATE = textwrap.dedent("""
Rank these items:

{items}

{output_instruction}
""").strip()


# Retry prompts for conversational error correction
INVALID_JSON_RETRY = 'The previous response was not valid JSON. Return a valid JSON array of IDs: ["id1", "id2", ...]'

MISSING_IDS_RETRY = "The previous response was missing these IDs: {missing_ids}. Return a complete ranking with ALL IDs."

EXTRA_IDS_RETRY = "The previous response included IDs that don't exist: {extra_ids}. Return only the IDs that were provided."


class RankableItem(TypedDict):
    id: str | int
    text: str


def format_items(items: list[RankableItem]) -> str:
    return "\n\n".join([f"id: `{item['id']}`\ntext: {item['text']}" for item in items])


def _build_length_requirement(
    min_items: int | None,
    max_items: int | None,
    force_return_all: bool,
) -> str:
    """Create length-related instructions for the system prompt.

    If ``force_return_all`` is True or no limits are provided, enforces returning all IDs.
    Otherwise, constrains the output length to the provided bounds.
    """
    if force_return_all or (min_items is None and max_items is None):
        return (
            "- Include ALL item IDs exactly as provided - do not skip, modify, or add IDs\n"
            "- Each ID must appear exactly once; the array length must equal the number of input items"
        )

    if min_items == 1 and max_items == 1:
        return (
            "- Return exactly 1 ID (the single best match)\n"
            "- Use one of the provided IDs; do not invent or modify IDs\n"
            "- Do not include duplicate IDs"
        )

    parts: list[str] = [
        "- Use only the provided IDs; do not invent or modify IDs",
        "- Do not include duplicate IDs",
    ]
    if min_items is not None and max_items is not None:
        parts.insert(
            0,
            f"- Return only the top IDs, length between {min_items} and {max_items} inclusive",
        )
    elif max_items is not None:
        parts.insert(0, f"- Return only the top IDs, length at most {max_items}")
    elif min_items is not None:
        parts.insert(0, f"- Return only the top IDs, length at least {min_items}")
    return "\n".join(parts)


def _build_output_instruction(
    min_items: int | None,
    max_items: int | None,
    force_return_all: bool,
) -> str:
    """Create the user prompt instruction for output shape."""
    if force_return_all or (min_items is None and max_items is None):
        return "Return a JSON array of IDs only, containing ALL items in ranked order."
    if min_items == 1 and max_items == 1:
        return "Return a JSON array with exactly 1 ID (the single best match)."
    if min_items is not None and max_items is not None:
        return f"Return a JSON array of IDs only, length between {min_items} and {max_items} inclusive."
    if max_items is not None:
        return f"Return a JSON array of IDs only, with at most {max_items} IDs."
    # min only
    return f"Return a JSON array of IDs only, with at least {min_items} IDs."


async def _build_ranking_agent(
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    model_settings: dict[str, Any] | None = None,
    retries: int = 3,
    base_url: str | None = None,
    *,
    min_items: int | None = None,
    max_items: int | None = None,
    force_return_all: bool = False,
) -> Agent:
    """Build an agent with ranking system instructions.

    Args:
        criteria_prompt: Natural language criteria for ranking.
        model_name: LLM model to use.
        model_provider: LLM provider.
        model_settings: Optional model settings.
        retries: Number of retries on failure.
        base_url: Optional base URL for custom providers.
        min_items: Minimum number of items to return (optional).
        max_items: Maximum number of items to return (optional).
        force_return_all: If True, instructs the model to return all items regardless of min/max.
    """
    instructions = RANKING_SYSTEM_PROMPT_TEMPLATE.format(
        criteria_prompt=criteria_prompt,
        length_requirement=_build_length_requirement(
            min_items, max_items, force_return_all
        ),
    )

    return await build_agent(
        AgentConfig(
            instructions=instructions,
            model_name=model_name,
            model_provider=model_provider,
            model_settings=model_settings,
            retries=retries,
            base_url=base_url,
        )
    )


async def _run_ranking_agent(
    agent: Agent[Any, Any],
    items: list[RankableItem],
    max_requests: int,
    *,
    min_items: int | None = None,
    max_items: int | None = None,
    force_return_all: bool = False,
) -> AgentOutput:
    """Run the ranking agent with items to rank."""
    user_prompt = RANKING_USER_PROMPT_TEMPLATE.format(
        items=format_items(items),
        output_instruction=_build_output_instruction(
            min_items, max_items, force_return_all
        ),
    )
    return await run_agent_sync(agent, user_prompt, max_requests=max_requests)


async def rank_items(
    items: list[RankableItem],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    model_settings: dict[str, Any] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
    *,
    min_items: int | None = None,
    max_items: int | None = None,
) -> list[str | int]:
    """Rank items using an LLM based on natural language criteria.

    Args:
        items: List of items to rank.
        criteria_prompt: Natural language criteria for ranking.
        model_name: LLM model to use.
        model_provider: LLM provider.
        model_settings: Optional model settings.
        max_requests: Maximum number of LLM requests.
        retries: Number of retries on failure.
        base_url: Optional base URL for custom providers.

    Returns:
        List of item IDs in ranked order (most to least relevant according to criteria).

    Raises:
        ValueError: If items are empty or too many items to rank.
        ValueError: If LLM response cannot be parsed or is invalid.
    """
    if not items:
        return []

    if len(items) > MAX_ITEMS:
        raise ValueError(f"Expected at most {MAX_ITEMS} items, got {len(items)} items.")

    agent = await _build_ranking_agent(
        criteria_prompt=criteria_prompt,
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        retries=retries,
        base_url=base_url,
        min_items=min_items,
        max_items=max_items,
        force_return_all=False,
    )
    result = await _run_ranking_agent(
        agent,
        items,
        max_requests,
        min_items=min_items,
        max_items=max_items,
        force_return_all=False,
    )

    valid_ids: set[str | int] = {item["id"] for item in items}
    output: list[str | int] = _sanitize_ids(result.output, valid_ids)

    # If the model returned more than max, trim locally for compliance
    if max_items is not None and len(output) > max_items:
        output = output[:max_items]

    # If the model returned fewer than min, do not attempt to pad; callers may handle
    return output


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


def _assign_worst_scores(
    scores: dict[str | int, list[float]],
    item_ids: list[str | int],
    worst_position: float,
) -> None:
    """Ensure every item has a fallback score when ranking fails."""
    for item_id in item_ids:
        if item_id not in scores:
            scores[item_id] = []
        scores[item_id].append(float(worst_position))


def _sanitize_ids(
    returned_ids: list[str | int],
    valid_ids: set[str | int],
) -> list[str | int]:
    """Normalize model outputs to valid IDs.

    - Strip wrapping backticks from string IDs (e.g., `id` -> id)
    - Convert purely numeric strings to int when appropriate
    - Keep only IDs that exist in the provided valid set and drop duplicates, preserving order
    """

    def normalize(value: str | int) -> str | int:
        if isinstance(value, int):
            return value
        s = value.strip()
        if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
            s = s[1:-1]
        # Prefer exact string match
        if s in valid_ids:
            return s
        # Try converting to int if possible and valid
        try:
            num = int(s)
            if num in valid_ids:
                return num
        except Exception:
            pass
        return s

    seen: set[str | int] = set()
    filtered: list[str | int] = []
    for raw in returned_ids:
        norm = normalize(raw)
        if norm in valid_ids and norm not in seen:
            filtered.append(norm)
            seen.add(norm)
    return filtered


async def _rank_batch(
    batch: list[RankableItem],
    agent: Any,
    max_requests: int,
) -> list[str | int]:
    """Rank a single batch of items using LLM.

    Args:
        batch: List of items to rank
        agent: Pre-built agent instance
        max_requests: Maximum number of LLM requests

    Returns:
        List of item IDs in ranked order (most to least relevant)

    Raises:
        ValueError: If LLM response cannot be parsed or is invalid
    """
    # For batch ranking within pairwise, we must rank ALL items in the batch
    result = await _run_ranking_agent(
        agent,
        batch,
        max_requests,
        min_items=None,
        max_items=None,
        force_return_all=True,
    )
    batch_ids: set[str | int] = {item["id"] for item in batch}
    sanitized = _sanitize_ids(result.output, batch_ids)
    return sanitized


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
    *,
    min_items: int | None = None,
    max_items: int | None = None,
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
        List of item IDs in ranked order (most to least relevant according to criteria)

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
        raise ValueError(f"Expected at most {MAX_ITEMS} items, got {len(items)} items.")

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
    agent = await _build_ranking_agent(
        criteria_prompt=criteria_prompt,
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        retries=retries,
        base_url=base_url,
        force_return_all=True,
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

    # Apply output limits if provided
    if min_items is None and max_items is None:
        return ranked_ids

    return ranked_ids[:max_items]


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
        shuffled_items = random.sample(items, len(items))

        # Create batches
        batches = _create_batches(shuffled_items, batch_size)

        # Rank all batches in parallel
        batch_tasks = [
            _rank_batch(
                batch=batch,
                agent=agent,
                max_requests=max_requests,
            )
            for batch in batches
        ]

        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

        # Process results and collect scores
        for batch_idx, result in enumerate(batch_results):
            batch = batches[batch_idx]
            batch_ids = [item[id_field] for item in batch]

            if isinstance(result, Exception):
                logger.warning(
                    "Batch ranking failed",
                    pass_num=pass_num,
                    batch_idx=batch_idx,
                    error=str(result),
                )
                _assign_worst_scores(scores, batch_ids, len(batch))
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
                _assign_worst_scores(scores, batch_ids, len(batch))
                continue

            ranked_ids: list[str | int] = result
            batch_id_set = set(batch_ids)

            # Filter out hallucinated IDs that aren't part of this batch
            filtered_ranked_ids = [
                item_id for item_id in ranked_ids if item_id in batch_id_set
            ]
            hallucinated_ids = [
                item_id for item_id in ranked_ids if item_id not in batch_id_set
            ]

            if hallucinated_ids:
                logger.warning(
                    "LLM returned IDs outside current batch",
                    pass_num=pass_num,
                    batch_idx=batch_idx,
                    hallucinated_count=len(hallucinated_ids),
                )

            # Assign positional scores (0-based index within batch)
            # Lower position = better rank = lower score
            for position, item_id in enumerate(filtered_ranked_ids):
                if item_id not in scores:
                    scores[item_id] = []
                scores[item_id].append(float(position))

            # Handle items that weren't returned by LLM
            returned_ids: set[str | int] = set(filtered_ranked_ids)
            missing_ids = batch_id_set - returned_ids

            if missing_ids:
                logger.warning(
                    "LLM did not return all items",
                    pass_num=pass_num,
                    batch_idx=batch_idx,
                    missing_count=len(missing_ids),
                )
                # Assign worst position to missing items
                worst_position = len(batch_ids)
                _assign_worst_scores(scores, list(missing_ids), worst_position)

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
