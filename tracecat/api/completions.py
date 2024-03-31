import asyncio
import inspect
from typing import TypeVar

from pydantic import BaseModel
from slugify import slugify

from tracecat.llm import async_openai_call
from tracecat.logger import standard_logger
from tracecat.types.cases import Case

logger = standard_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class CategoryConstraint(BaseModel):
    tag: str
    value: list[str] = []


def model_as_text(cls: type[T]) -> str:
    """Return the class definition (excluding functions and methods) as a string."""
    lines = [f"class {cls.__name__}(BaseModel):"]
    # Iterate over the annotations to get the attribute names and types
    for attr, attr_type in cls.__annotations__.items():
        # For optional attributes, include the default value if it exists
        default_value = ""
        if attr in cls.model_fields:
            field_info = cls.model_fields[attr]
            if not field_info.is_required():
                default_value = f" = {field_info.default}"
        line = f"    {attr}: {attr_type}{default_value}"
        lines.append(line)
    return "\n".join(lines)


__ACTION_CONSTRAINT_REPLACE__ = str
__CONTEXT_CONSTRAINT_REPLACE__ = str


class CaseMissingFieldsResponse(BaseModel):
    """The response model for missing fields in a Case object.

    Params
    ------
    context: dict[Literal[<CATEGORY_CONSTRAINT.tag>], Literal[<CATEGORY_CONSTRAINT.value>]]
        The context of the case, represented as key-value pairs of `CATEGORY_CONSTRAINT` 1-to-1 tag-value mappings.
        You MUST only use the valid tag-value combinations within the provided disciminated unions (prefixed with `_CATEGORY_CONSTRAINT_`).
    action: Literal[...]
        The action to be taken for the case.
    """

    context: dict[str, __CONTEXT_CONSTRAINT_REPLACE__]
    action: __ACTION_CONSTRAINT_REPLACE__


def _dynamic_constraint_factory(
    cls: type[T], *, cons_types: dict[str, list[str] | list[CategoryConstraint] | None]
) -> str:
    """Performs replacements of magic strings with concrete types."""
    src = inspect.getsource(cls)
    all_supporting_types = []
    for placeholder, value in cons_types.items():
        if all(isinstance(v, CategoryConstraint) for v in value):
            # Map this into a disciminated union
            inner, supporting_types = _to_disciminated_union(value)
            all_supporting_types.append(supporting_types)
        elif isinstance(value, list):
            inner = f"Literal[{",".join(f"{v!r}" for v in value)}]"
        elif isinstance(value, str):
            inner = f"Literal[{value!r}]"
        elif value is None:
            # If the constraint value is None, we should use the Any type
            inner = "typing.Any"
        else:
            raise ValueError(f"Unsupported type: {type(value)}")

        # Make the Literal optional so the model can return an "IDK" case
        src = src.replace(
            placeholder, inner + " | Literal['Please investigate further']"
        )
    return src, all_supporting_types


def _to_disciminated_union(cons: list[CategoryConstraint]):
    logger.info(f"Creating discriminated union for {cons =}")
    supporting_tags = {}
    for tc in cons:
        tag = tc.tag
        values = tc.value
        classname = f"_CATEGORY_CONSTRAINT_{slugify(tag, separator='_').upper()}"
        # if there are no values, we should use the Any type
        value_type = (
            f"Literal[{", ".join(f"{v!r}" for v in values)}]"
            if values  # Catch empty list or None
            else "typing.Any"
        )
        fmt_tag = (
            f"class {classname}(BaseModel):"
            f"    tag: Literal['{tag}'] = Field('{tag}', frozen=True)"
            f"    value: {value_type}"
        )
        supporting_tags[classname] = fmt_tag
    union_type = " | ".join(supporting_tags.keys())
    supporting_types = "\n\n".join(supporting_tags.values())
    return union_type, supporting_types


def _generate_pydantic_model_json_response_schema(
    cls: type[T],
    *,
    cons_types: dict[str, list[CategoryConstraint] | None] = None,
) -> str:
    constrained_cls, supporting_types = _dynamic_constraint_factory(
        cls, cons_types=cons_types
    )
    return (
        f"\nCPlease complete the `{cls.__name__}` according to the following"
        " pydantic model and discriminated unions:"
        "\n```"
        f"\n{constrained_cls}"
        f"\n\n{"\n\n".join(supporting_types)}"
        "\n```"
        "\nFor each discriminated union, you should provide the appropriate value."
    )


def _case_completions_system_context(
    *,
    output_cls: type[T] = type[CaseMissingFieldsResponse],
    context_cons: list[CategoryConstraint] | None = None,
    action_cons: list[CategoryConstraint] | None = None,
) -> str:
    # Concretize the magic strings in the output_cls
    cons_types = {
        # NOTE: There currently only is 1 tag for action_cons: case_action
        "__ACTION_CONSTRAINT_REPLACE__": action_cons[0].value if action_cons else None,
        "__CONTEXT_CONSTRAINT_REPLACE__": context_cons,
    }
    output_schema_prompt = _generate_pydantic_model_json_response_schema(
        output_cls, cons_types=cons_types
    )
    system_context = (
        "\nYou are an expert at completing cases (Case objects), which are created from alerts."
        f"\nThis is the Case pydantic schema: ```\n{model_as_text(Case)}\n```"
        "\nYou will be provided with a Case object in the form of a JSON object"
        " delimited by triple backticks (```)."
        " Your task is to fill in the missing case fields denoted by null types (e.g. 'None', 'null' etc.)"
        " to the best of your ability."
        " If you don't know how to complete a field, please put exactly 'Please investigate further'."
        f"\n{output_schema_prompt}"
    )
    return system_context


class CaseCompletionResponse(BaseModel):
    id: str
    response: CaseMissingFieldsResponse


async def stream_case_completions(
    cases: list[Case],
    *,
    context_cons: list[CategoryConstraint] | None = None,
    action_cons: list[CategoryConstraint] | None = None,
):
    """Given a list of cases, fill in the missing fields for each.

    Approach
    --------
    - We might want to batch these

    """

    system_context = _case_completions_system_context(
        output_cls=CaseMissingFieldsResponse,
        context_cons=context_cons,
        action_cons=action_cons,
    )
    logger.info(f"🧠 Starting case completions for %d cases... {system_context =}")

    async def task(case: Case) -> str:
        prompt = f"""Case JSON Object: ```\n{case.model_dump_json()}\n```"""
        logger.info(f"🧠 Starting case completion for case {case.id}...")
        response: dict[str, str] = await async_openai_call(
            prompt=prompt,
            model="gpt-4-turbo-preview",
            system_context=system_context,
            response_format="json_object",
            max_tokens=200,
        )
        # We might have to perform additional matching / postprocessing here
        # Depending on what we return.
        result = CaseCompletionResponse.model_validate(
            {"id": case.id, "response": response}
        )
        # await asyncio.sleep(random.uniform(1, 10))
        logger.info(f"🧠 Completed case completion for case {case.id}")
        return result.model_dump_json()

    tasks = [task(case) for case in cases]

    for coro in asyncio.as_completed(tasks, timeout=120):
        result = await coro
        yield result + "\n"
    logger.info("🧠 Completed all case completions.")
