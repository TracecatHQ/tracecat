"""LLM-based ranking helpers for registry actions.

This module is a registry-owned adaptation of the previous core implementation.
It performs ranking by calling back into Tracecat's executor AI endpoint via the
registry SDK context (`get_context().agent.run_action`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import textwrap
from typing import Any, Sequence, TypedDict

from tracecat_registry.context import get_context

logger = logging.getLogger(__name__)

# Global constraint to defend against resource consumption attacks
MAX_ITEMS: int = 100


RANKING_SYSTEM_PROMPT_TEMPLATE = textwrap.dedent(
    """
You are a ranking assistant. Your task is to rank items based on the given criteria.

Criteria:
{criteria_prompt}

Requirements:
- Rank each item against the criteria from most to least relevant
{length_requirement}
- Return ONLY a JSON array of IDs in ranked order: ["id1", "id2", ...]
- Do not include explanations, reasoning, markdown formatting, or other text
- The response must be valid deserializable JSON array (start and ends with square brackets)
"""
).strip()


RANKING_USER_PROMPT_TEMPLATE = textwrap.dedent(
    """
Rank these items:

{items}

{output_instruction}
"""
).strip()


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
    if force_return_all or (min_items is None and max_items is None):
        return "Return a JSON array of IDs only, containing ALL items in ranked order."
    if min_items == 1 and max_items == 1:
        return "Return a JSON array with exactly 1 ID (the single best match)."
    if min_items is not None and max_items is not None:
        return f"Return a JSON array of IDs only, length between {min_items} and {max_items} inclusive."
    if max_items is not None:
        return f"Return a JSON array of IDs only, with at most {max_items} IDs."
    return f"Return a JSON array of IDs only, with at least {min_items} IDs."


def _extract_action_output(response: Any) -> Any:
    if isinstance(response, dict) and "output" in response:
        return response["output"]
    return response


async def _run_ranking_call(
    *,
    criteria_prompt: str,
    items: list[RankableItem],
    model_name: str,
    model_provider: str,
    model_settings: dict[str, Any] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
    force_return_all: bool = False,
) -> list[str | int]:
    instructions = RANKING_SYSTEM_PROMPT_TEMPLATE.format(
        criteria_prompt=criteria_prompt,
        length_requirement=_build_length_requirement(
            min_items, max_items, force_return_all
        ),
    )
    user_prompt = RANKING_USER_PROMPT_TEMPLATE.format(
        items=format_items(items),
        output_instruction=_build_output_instruction(
            min_items, max_items, force_return_all
        ),
    )

    ctx = get_context()
    response = await ctx.agent.run_action(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        instructions=instructions,
        output_type="list[str]",
        model_settings=model_settings,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
    )
    output = _extract_action_output(response)
    if not isinstance(output, list):
        raise ValueError("Ranking output is not a list")
    return output


def _create_batches(
    items: list[RankableItem], batch_size: int
) -> list[list[RankableItem]]:
    batches: list[list[RankableItem]] = []
    for i in range(0, len(items), batch_size):
        batches.append(items[i : i + batch_size])
    return batches


def _average_scores(scores: dict[str | int, list[float]]) -> dict[str | int, float]:
    return {
        item_id: sum(score_list) / len(score_list)
        for item_id, score_list in scores.items()
    }


def _assign_worst_scores(
    scores: dict[str | int, list[float]],
    item_ids: list[str | int],
    worst_position: float,
) -> None:
    for item_id in item_ids:
        if item_id not in scores:
            scores[item_id] = []
        scores[item_id].append(float(worst_position))


def _sanitize_ids(
    returned_ids: Sequence[str | int],
    valid_ids: set[str | int],
) -> list[str | int]:
    def normalize(value: str | int) -> str | int:
        if isinstance(value, int):
            return value
        s = value.strip()
        if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
            s = s[1:-1]
        if s in valid_ids:
            return s
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
    *,
    batch: list[RankableItem],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    model_settings: dict[str, Any] | None,
    max_requests: int,
    retries: int,
    base_url: str | None,
) -> list[str | int]:
    # Within pairwise, we must rank ALL items in the batch
    output = await _run_ranking_call(
        criteria_prompt=criteria_prompt,
        items=batch,
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
        force_return_all=True,
    )
    batch_ids: set[str | int] = {item["id"] for item in batch}
    return _sanitize_ids(output, batch_ids)


async def rank_items(
    *,
    items: list[RankableItem],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    model_settings: dict[str, Any] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
) -> list[str | int]:
    if not items:
        return []
    if len(items) > MAX_ITEMS:
        raise ValueError(f"Expected at most {MAX_ITEMS} items, got {len(items)} items.")

    valid_ids: set[str | int] = {item["id"] for item in items}
    output = await _run_ranking_call(
        criteria_prompt=criteria_prompt,
        items=items,
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
        min_items=min_items,
        max_items=max_items,
        force_return_all=False,
    )
    sanitized = _sanitize_ids(output, valid_ids)
    if max_items is not None and len(sanitized) > max_items:
        return sanitized[:max_items]
    return sanitized


async def rank_items_pairwise(
    *,
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
    min_items: int | None = None,
    max_items: int | None = None,
) -> list[str | int]:
    if not items:
        return []
    if len(items) > MAX_ITEMS:
        raise ValueError(f"Expected at most {MAX_ITEMS} items, got {len(items)} items.")
    for idx, item in enumerate(items):
        if id_field not in item:
            raise ValueError(
                f"Item at index {idx} missing required field '{id_field}': {item}"
            )
    if len(items) == 1:
        return [items[0][id_field]]

    ranked_ids = await _multi_pass_rank(
        items=items,
        criteria_prompt=criteria_prompt,
        id_field=id_field,
        batch_size=batch_size,
        num_passes=num_passes,
        refinement_ratio=refinement_ratio,
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
        depth=0,
    )
    if min_items is None and max_items is None:
        return ranked_ids
    return ranked_ids[:max_items]


async def _multi_pass_rank(
    *,
    items: list[RankableItem],
    criteria_prompt: str,
    id_field: str,
    batch_size: int,
    num_passes: int,
    refinement_ratio: float,
    model_name: str,
    model_provider: str,
    model_settings: dict[str, Any] | None,
    max_requests: int,
    retries: int,
    base_url: str | None,
    depth: int = 0,
) -> list[str | int]:
    if len(items) <= 1:
        return [items[0][id_field]] if items else []

    logger.info(
        "Multi-pass ranking",
        extra={
            "depth": depth,
            "num_items": len(items),
            "num_passes": num_passes,
            "batch_size": batch_size,
        },
    )

    scores: dict[str | int, list[float]] = {}

    for pass_num in range(num_passes):
        shuffled_items = random.sample(items, len(items))
        batches = _create_batches(shuffled_items, batch_size)

        batch_tasks = [
            _rank_batch(
                batch=batch,
                criteria_prompt=criteria_prompt,
                model_name=model_name,
                model_provider=model_provider,
                model_settings=model_settings,
                max_requests=max_requests,
                retries=retries,
                base_url=base_url,
            )
            for batch in batches
        ]
        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

        for batch_idx, result in enumerate(batch_results):
            batch = batches[batch_idx]
            batch_ids = [item[id_field] for item in batch]

            if isinstance(result, Exception):
                logger.warning(
                    "Batch ranking failed",
                    extra={
                        "pass_num": pass_num,
                        "batch_idx": batch_idx,
                        "error": str(result),
                    },
                )
                _assign_worst_scores(scores, batch_ids, len(batch))
                continue

            ranked_ids: list[str | int] = result if isinstance(result, list) else []
            batch_id_set = set(batch_ids)

            filtered_ranked_ids = [
                item_id for item_id in ranked_ids if item_id in batch_id_set
            ]
            hallucinated_ids = [
                item_id for item_id in ranked_ids if item_id not in batch_id_set
            ]
            if hallucinated_ids:
                logger.warning(
                    "LLM returned IDs outside current batch",
                    extra={
                        "pass_num": pass_num,
                        "batch_idx": batch_idx,
                        "hallucinated_count": len(hallucinated_ids),
                    },
                )

            for position, item_id in enumerate(filtered_ranked_ids):
                if item_id not in scores:
                    scores[item_id] = []
                scores[item_id].append(float(position))

            returned_ids: set[str | int] = set(filtered_ranked_ids)
            missing_ids = batch_id_set - returned_ids
            if missing_ids:
                logger.warning(
                    "LLM did not return all items",
                    extra={
                        "pass_num": pass_num,
                        "batch_idx": batch_idx,
                        "missing_count": len(missing_ids),
                    },
                )
                _assign_worst_scores(scores, list(missing_ids), len(batch_ids))

    avg_scores = _average_scores(scores)
    sorted_ids = sorted(avg_scores.keys(), key=lambda x: avg_scores[x])

    if refinement_ratio > 0 and len(sorted_ids) > 1:
        refinement_count = max(1, int(len(sorted_ids) * refinement_ratio))
        if refinement_count < len(sorted_ids):
            top_ids = sorted_ids[:refinement_count]
            bottom_ids = sorted_ids[refinement_count:]
            top_items = [item for item in items if item[id_field] in top_ids]
            refined_top_ids = await _multi_pass_rank(
                items=top_items,
                criteria_prompt=criteria_prompt,
                id_field=id_field,
                batch_size=batch_size,
                num_passes=max(1, num_passes // 2),
                refinement_ratio=refinement_ratio,
                model_name=model_name,
                model_provider=model_provider,
                model_settings=model_settings,
                max_requests=max_requests,
                retries=retries,
                base_url=base_url,
                depth=depth + 1,
            )
            return refined_top_ids + bottom_ids

    return sorted_ids
