from tracecat_registry import registry
from typing import Annotated, Any
from typing_extensions import Doc


@registry.register(
    default_title="AI ranker",
    description="Use AI to rank a list of text documents according to a specified criteria using the pairwise ranking prompting algorithm.",
    namespace="ai",
)
def rank_documents(
    items: Annotated[
        list[str],
        Doc("List of text documents to rank."),
    ],
    criteria_prompt: Annotated[
        str,
        Doc("Criteria to use for ranking."),
    ],
):
    pass


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
):
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
):
    pass
