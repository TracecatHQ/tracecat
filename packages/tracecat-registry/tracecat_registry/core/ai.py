"""AI utilities. Actions that use LLMs to perform specific predefined tasks."""

from typing import Annotated, Any, Literal

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.fields import AgentModel, ModelSelection

DEFAULT_RANKING_ALGORITHM: Literal["single-pass", "pairwise"] = "single-pass"
DEFAULT_RANKING_BATCH_SIZE: int = 10
DEFAULT_RANKING_NUM_PASSES: int = 10
DEFAULT_RANKING_REFINEMENT_RATIO: float = 0.5


@registry.register(
    default_title="Rank documents",
    description="Use AI to rank a list of text documents from best to worst according to a specified criteria.",
    namespace="ai",
    deprecated="Use the 'ai.agent' action instead.",
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
    model: Annotated[
        ModelSelection | None,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("Deprecated model name. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated model provider. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
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
    raise NotImplementedError("Use the 'ai.agent' action instead.")


@registry.register(
    default_title="Select one field",
    description="Use AI to select one field from a JSON object. The field name and field value are returned as a dict with `key` and `value`.",
    namespace="ai",
    deprecated="Use the 'ai.agent' action instead.",
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
    model: Annotated[
        ModelSelection | None,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("Deprecated model name. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated model provider. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
    algorithm: Annotated[
        Literal["single-pass", "pairwise"],
        Doc("Algorithm to use for ranking."),
    ] = DEFAULT_RANKING_ALGORITHM,
) -> dict[str, Any]:
    raise NotImplementedError("Use the 'ai.agent' action instead.")


@registry.register(
    default_title="Select many fields",
    description="Use AI to select and rank fields from a JSON object. Returns the JSON object with only the selected fields.",
    namespace="ai",
    deprecated="Use the 'ai.agent' action instead.",
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
    model: Annotated[
        ModelSelection | None,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("Deprecated model name. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated model provider. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
    algorithm: Annotated[
        Literal["single-pass", "pairwise"],
        Doc("Algorithm to use for ranking."),
    ] = DEFAULT_RANKING_ALGORITHM,
) -> dict[str, Any]:
    raise NotImplementedError("Use the 'ai.agent' action instead.")
