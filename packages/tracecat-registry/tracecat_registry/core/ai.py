"""AI utilities. Actions that use LLMs to perform specific predefined tasks."""

from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.ai.ranker import RankableItem, rank_items_pairwise
from tracecat_registry import registry


@registry.register(
    default_title="AI ranker",
    description="Use AI to rank a list of text documents according to a specified criteria using the pairwise ranking prompting algorithm.",
    namespace="ai",
)
async def rank_documents(
    items: Annotated[
        list[str],
        Doc("List of text documents to rank."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc(
            "Natural language criteria for ranking (e.g., 'by severity', 'most relevant to security')."
        ),
    ],
    model_name: Annotated[
        str,
        Doc("LLM model to use for ranking."),
    ] = "gpt-5-mini-2025-08-07",
    model_provider: Annotated[
        str,
        Doc("LLM provider (e.g., 'openai', 'anthropic')."),
    ] = "openai",
    batch_size: Annotated[
        int,
        Doc("Number of items per batch for ranking."),
    ] = 10,
    num_passes: Annotated[
        int,
        Doc("Number of shuffle-batch-rank iterations to reduce positional bias."),
    ] = 10,
    refinement_ratio: Annotated[
        float,
        Doc("Portion of top items to recursively refine (0-1, default 0.5)."),
    ] = 0.5,
) -> list[str]:
    """Rank items using multi-pass pairwise LLM ranking with progressive refinement.

    This implements the BishopFox raink algorithm:
    1. Multiple passes of shuffle→batch→rank to reduce positional bias
    2. Average positional scores across all passes for robust ranking
    3. Progressive refinement: recursively re-rank top candidates
    4. Parallel LLM calls for batch processing

    Returns ranked items in order from best to worst according to criteria.
    """

    if len(items) < 3:
        raise ValueError(f"Expected at least 3 items to rank, got {len(items)} items.")

    dict_items: list[RankableItem] = [
        {"id": i, "text": item} for i, item in enumerate(items)
    ]
    # Perform pairwise ranking
    ranked_ids = await rank_items_pairwise(
        items=dict_items,
        criteria_prompt=criteria_prompt,
        model_name=model_name,
        model_provider=model_provider,
        id_field="id",
        batch_size=batch_size,
        num_passes=num_passes,
        refinement_ratio=refinement_ratio,
    )

    # Map back to original strings
    id_to_text: dict[int, str] = {i: text for i, text in enumerate(items)}
    return [id_to_text[int(item_id)] for item_id in ranked_ids]


@registry.register(
    default_title="Extract field",
    description="Use AI to extract a field from a JSON object. The field name and field value are returned as a dict with `key` and `value`.",
    namespace="ai",
)
def extract_field(
    json: Annotated[
        dict[str, Any],
        Doc("JSON object to extract the field from."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc("Criteria to use for extracting the field."),
    ],
    flatten: Annotated[
        bool,
        Doc(
            "Extract from and return a flattened single level object with JSONPath notation as keys."
        ),
    ] = False,
) -> None:
    pass


@registry.register(
    default_title="Select fields",
    description="Use AI to select fields from a JSON object. Returns the JSON object with only the selected fields.",
    namespace="ai",
)
def select_fields(
    json: Annotated[
        dict[str, Any],
        Doc("JSON object to select the fields from."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc("Criteria to use for selecting the fields."),
    ],
    flatten: Annotated[
        bool,
        Doc(
            "Extract from and return a flattened single level object with JSONPath notation as keys."
        ),
    ] = False,
) -> None:
    pass
