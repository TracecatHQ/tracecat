"""AI utilities. Actions that use LLMs to perform specific predefined tasks."""

from typing import Annotated, Any, Literal, TypedDict

from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat_registry.core.transform import flatten_dict
from tracecat_registry.sdk.agents import RankableItem, rank_items, rank_items_pairwise


MAX_KEYS: int = 100
"""Maximum number of keys rankable by the AI."""


DEFAULT_RANKING_MODEL: str = "gpt-5-nano-2025-08-07"
"""Default ranking model to use."""

DEFAULT_RANKING_MODEL_PROVIDER: str = "openai"
"""Default ranking model provider to use."""

DEFAULT_RANKING_ALGORITHM: Literal["single-pass", "pairwise"] = "single-pass"
"""Default ranking algorithm to use."""

DEFAULT_RANKING_BATCH_SIZE: int = 10
"""Default ranking batch size to use."""

DEFAULT_RANKING_NUM_PASSES: int = 10
"""Default ranking number of passes to use."""

DEFAULT_RANKING_REFINEMENT_RATIO: float = 0.5
"""Default ranking refinement ratio to use."""


@registry.register(
    default_title="Rank documents",
    description="Use AI to rank a list of text documents from best to worst according to a specified criteria.",
    namespace="ai",
    secrets=PYDANTIC_AI_REGISTRY_SECRETS,
)
async def rank_documents(
    items: Annotated[
        list[str],
        Doc("List of text documents to rank."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc(
            'Criteria to rank the items by. For example, "from most to least important."'
        ),
    ],
    model_name: Annotated[
        str,
        Doc("LLM model to use for ranking."),
    ] = DEFAULT_RANKING_MODEL,
    model_provider: Annotated[
        str,
        Doc("LLM provider (e.g., 'openai', 'anthropic')."),
    ] = DEFAULT_RANKING_MODEL_PROVIDER,
    algorithm: Annotated[
        Literal["single-pass", "pairwise"],
        Doc("Algorithm to use for ranking."),
    ] = DEFAULT_RANKING_ALGORITHM,
    batch_size: Annotated[
        int,
        Doc("Number of items per batch for ranking."),
    ] = DEFAULT_RANKING_BATCH_SIZE,
    num_passes: Annotated[
        int,
        Doc("Number of shuffle-batch-rank iterations to reduce positional bias."),
    ] = DEFAULT_RANKING_NUM_PASSES,
    refinement_ratio: Annotated[
        float,
        Doc("Portion of top items to recursively refine (0-1, default 0.5)."),
    ] = DEFAULT_RANKING_REFINEMENT_RATIO,
) -> list[str]:
    """Rank items using multi-pass pairwise LLM ranking with progressive refinement.

    This implements the BishopFox raink algorithm:
    1. Multiple passes of shuffle→batch→rank to reduce positional bias
    2. Average positional scores across all passes for robust ranking
    3. Progressive refinement: recursively re-rank top candidates
    4. Parallel LLM calls for batch processing

    Returns ranked items in order from most to least relevant to the criteria.
    """

    if len(items) < 3:
        raise ValueError(f"Expected at least 3 items to rank, got {len(items)} items.")

    dict_items: list[RankableItem] = [
        {"id": i, "text": item} for i, item in enumerate(items)
    ]

    if algorithm == "pairwise":
        ranked_ids = await rank_items_pairwise(
            items=dict_items,
            criteria_prompt=criteria_prompt,
            model_name=model_name,
            model_provider=model_provider,
            id_field="id",
            batch_size=batch_size,
            num_passes=num_passes,
            refinement_ratio=refinement_ratio,
            min_items=1,
            max_items=1,
        )
    elif algorithm == "single-pass":
        ranked_ids = await rank_items(
            items=dict_items,
            criteria_prompt=criteria_prompt,
            model_name=model_name,
            model_provider=model_provider,
            min_items=1,
            max_items=1,
        )
    else:
        raise ValueError(
            f"Unsupported algorithm: {algorithm}. Expected 'pairwise' or 'single-pass'."
        )

    # Map back to original strings
    id_to_text: dict[int, str] = {i: text for i, text in enumerate(items)}
    return [id_to_text[int(item_id)] for item_id in ranked_ids]


def _get_keys(json: dict[str, Any]) -> list[RankableItem]:
    return [{"id": key, "text": str(key)} for key in json.keys()]


class ExtractFieldResult(TypedDict):
    key: str
    value: Any


@registry.register(
    default_title="Select one field",
    description="Use AI to select one field from a JSON object. The field name and field value are returned as a dict with `key` and `value`.",
    namespace="ai",
    secrets=PYDANTIC_AI_REGISTRY_SECRETS,
)
async def select_field(
    json: Annotated[
        dict[str, Any],
        Doc("JSON object to extract the field from."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc(
            'Criteria to determine which field to select. For example, "the name of the alert."'
        ),
    ],
    flatten: Annotated[
        bool,
        Doc(
            "Extract from and return a flattened single level object with JSONPath notation as keys."
        ),
    ] = False,
    model_name: Annotated[
        str,
        Doc("LLM model to use for ranking."),
    ] = DEFAULT_RANKING_MODEL,
    model_provider: Annotated[
        str,
        Doc("LLM provider (e.g., 'openai', 'anthropic')."),
    ] = DEFAULT_RANKING_MODEL_PROVIDER,
    algorithm: Annotated[
        Literal["single-pass", "pairwise"],
        Doc("Algorithm to use for ranking."),
    ] = DEFAULT_RANKING_ALGORITHM,
) -> ExtractFieldResult:
    if flatten:
        json = flatten_dict(json)

    if not json:
        raise ValueError("Expected JSON object with at least one key to rank.")

    # Get keys
    keys = _get_keys(json)

    if len(keys) > MAX_KEYS:
        raise ValueError(f"Expected at most {MAX_KEYS} keys, got {len(keys)} keys.")

    # Rank keys by criteria
    if algorithm == "pairwise":
        ranked_ids = await rank_items_pairwise(
            items=keys,
            criteria_prompt=criteria_prompt,
            model_name=model_name,
            model_provider=model_provider,
            min_items=1,
            max_items=1,
        )
    elif algorithm == "single-pass":
        ranked_ids = await rank_items(
            items=keys,
            criteria_prompt=criteria_prompt,
            model_name=model_name,
            model_provider=model_provider,
            min_items=1,
            max_items=1,
        )

    if not ranked_ids:
        raise ValueError("Ranking did not return any keys to extract.")

    # Get most relevant key
    selected_id = ranked_ids[0]
    most_relevant_key = str(selected_id)

    if most_relevant_key in json:
        most_relevant_value = json[most_relevant_key]
    elif selected_id in json:
        most_relevant_value = json[selected_id]
    else:
        raise KeyError(
            f"Ranked key '{most_relevant_key}' not found in JSON object keys."
        )
    return ExtractFieldResult(key=most_relevant_key, value=most_relevant_value)


@registry.register(
    default_title="Select many fields",
    description="Use AI to select and rank fields from a JSON object. Returns the JSON object with only the selected fields.",
    namespace="ai",
    secrets=PYDANTIC_AI_REGISTRY_SECRETS,
)
async def select_fields(
    json: Annotated[
        dict[str, Any],
        Doc("JSON object to select the fields from."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc(
            'Criteria to rank the fields by. For example, "from most to least important."'
        ),
    ],
    min_fields: Annotated[
        int,
        Doc("Minimum number of fields to select."),
    ] = 5,
    max_fields: Annotated[
        int,
        Doc("Maximum number of fields to select."),
    ] = 30,
    flatten: Annotated[
        bool,
        Doc(
            "Extract from and return a flattened single level object with JSONPath notation as keys."
        ),
    ] = False,
    model_name: Annotated[
        str,
        Doc("LLM model to use for ranking."),
    ] = DEFAULT_RANKING_MODEL,
    model_provider: Annotated[
        str,
        Doc("LLM provider (e.g., 'openai', 'anthropic')."),
    ] = DEFAULT_RANKING_MODEL_PROVIDER,
    algorithm: Annotated[
        Literal["single-pass", "pairwise"],
        Doc("Algorithm to use for ranking."),
    ] = DEFAULT_RANKING_ALGORITHM,
) -> dict[str, Any]:
    if flatten:
        json = flatten_dict(json)

    if not json:
        raise ValueError("Expected JSON object with at least one key to rank.")

    if len(json.keys()) > MAX_KEYS:
        raise ValueError(
            f"Expected at most {MAX_KEYS} keys, got {len(json.keys())} keys."
        )

    if max_fields < 2:
        raise ValueError("Number of fields to select must be at least 2.")

    # Get keys
    keys = _get_keys(json)
    # Rank keys by criteria
    if algorithm == "pairwise":
        ranked_ids = await rank_items_pairwise(
            items=keys,
            criteria_prompt=criteria_prompt,
            model_name=model_name,
            model_provider=model_provider,
            min_items=min_fields,
            max_items=max_fields,
        )
    elif algorithm == "single-pass":
        ranked_ids = await rank_items(
            items=keys,
            criteria_prompt=criteria_prompt,
            model_name=model_name,
            model_provider=model_provider,
            min_items=min_fields,
            max_items=max_fields,
        )

    if not ranked_ids:
        raise ValueError("Ranking did not return any keys to select.")

    # Get most relevant keys
    selected_fields: dict[str, Any] = {}
    for ranked_id in ranked_ids:
        key_str = str(ranked_id)
        if key_str in json:
            selected_fields[key_str] = json[key_str]
        elif ranked_id in json:
            selected_fields[key_str] = json[ranked_id]
        else:
            continue

        if len(selected_fields) >= max_fields:
            break

    if not selected_fields:
        raise ValueError("Unable to match ranked keys to JSON fields.")

    # Return JSON object with only the selected fields
    return selected_fields
