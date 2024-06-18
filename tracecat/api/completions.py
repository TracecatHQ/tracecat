from __future__ import annotations

import asyncio
import inspect
from typing import Annotated, Literal, TypeVar

from loguru import logger
from pydantic import BaseModel, Field
from slugify import slugify

from tracecat.llm import retryable_async_openai_call
from tracecat.types.cases import Case

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


type CaseField = Literal["tags", "action"]

type ConsType = Annotated[
    list[CategoryConstraint] | list[str] | None,
    "This can be: a list of strings (constrained selection), "
    "a list of CategoryConstraints (categorical constrained selection), or None (unconstrained/freeform).",
]

type FieldCons = dict[CaseField, ConsType]

# These magic placeholders are used for 2 reasons:
# 1. To use as substitution placeholders for concerete type constraints
# 2. Type checking of the actual output, to inform the general shape of the repsonse
__ACTION_CONSTRAINT_REPLACE__ = str


class __TAGS_CONSTRAINT_REPLACE__(BaseModel):
    tag: str
    value: str


case_field_to_placeholder: dict[CaseField, str] = {
    "action": "__ACTION_CONSTRAINT_REPLACE__",
    "tags": "__TAGS_CONSTRAINT_REPLACE__",
}

_UNCONSTRAINED_TYPE = "typing.Any"


# NOTE: Instructions for the `CaseMissingFieldsResponse` model.
# NOTE: Do not modify the docstring of the `CaseMissingFieldsResponse` model, as we use it to generate the LLM system context.

# NOTE: Implementation
# NOTE: --------------
# NOTE: We use magic placeholder strings in the model to represent the constraints for the `context` and `action` fields.
# NOTE: These placeholders are replaced with concrete types during runtime. This works because we use `inspect.getsource(cls)`
# NOTE: to get the source code of the model class. We can then replace the magic strings with the concrete types.

# NOTE: Usage
# NOTE: -----
# NOTE: - To extend this for other Case fields, you can add additional fields to the model.


class CaseMissingFieldsResponse(BaseModel):
    """The response model for missing fields in a Case object.

    Params
    ------
    tags: list[__SOME_CONSTRAINT__]
        __SOME_CONSTRAINT__.tag -> Literal[<CATEGORY_CONSTRAINT.tag>]
        __SOME_CONSTRAINT__.value -> Literal[<CATEGORY_CONSTRAINT.value>]
        The tags of the case, represented as a list of `CATEGORY_CONSTRAINT` 1-to-1 tag-value pairs.
        You MUST only use the valid tag-value combinations within the provided disciminated unions (prefixed with `_CATEGORY_CONSTRAINT_`).
    action: Literal[...]
        The action to be taken for the case.
    """

    tags: list[__TAGS_CONSTRAINT_REPLACE__] = Field(
        default=[], description="Empty list if you don't know how to tag the case."
    )


def _dynamic_constraint_factory(
    cls: type[T], *, field_cons: FieldCons
) -> tuple[str, list[str]]:
    """Performs replacements of placeholder strings with concrete types."""

    src = inspect.getsource(cls)
    all_supporting_types: list[str] = []

    # TODO(perf): One shot replace
    for field, maybe_cons in field_cons.items():
        if maybe_cons is None:
            # Unconstrained selection / freeform
            inner = _UNCONSTRAINED_TYPE
        elif isinstance(maybe_cons, list):
            if all(isinstance(v, CategoryConstraint) for v in maybe_cons):
                # Categorical constrained selection: Select values wrt a set of categories
                # >> Map into a disciminated union
                inner, supporting_types = _to_disciminated_union(maybe_cons)
                all_supporting_types.append(supporting_types)
            else:
                # Contrained selection: Select values from a set
                # >> Literal union for list of strings
                inner = _to_literal_union(maybe_cons)
        else:
            raise ValueError(f"Unsupported type: {type(maybe_cons)}")

        # Make the Literal optional so the model can return an "IDK" case
        placeholder = case_field_to_placeholder[field]
        src = src.replace(placeholder, inner)
    return src, all_supporting_types


def _to_literal_union(values: list[str]) -> str:
    """Generate a Literal union type for a list of values.

    Args:
        values (list[str]): List of values

    Returns:
        str: The Literal union type
    """
    return f"Literal[{",".join(f"{v!r}" for v in values)}]"


def _to_disciminated_union(cons: list[CategoryConstraint]) -> tuple[str, str]:
    """Generate a discriminated union type for a list of CategoryConstraints.

    Args:
        cons (list[CategoryConstraint]): List of CategoryConstraints

    Returns:
        tuple[str, str]: The discriminated union type and the supporting types
    """
    logger.info("Creating discriminated union", cons=cons)
    supporting_tags = {}
    for tc in cons:
        tag = tc.tag
        values = tc.value
        classname = f"_CATEGORY_CONSTRAINT_{slugify(tag, separator='_').upper()}"
        # if there are no values, we should use the Any type
        value_type = (
            _to_literal_union(values)
            if values  # Catch empty list or None
            else _UNCONSTRAINED_TYPE
        )
        fmt_tag = (
            f"class {classname}(BaseModel):"
            f"\n    tag: Literal['{tag}'] = Field('{tag}', frozen=True)"
            f"\n    value: {value_type}"
        )
        supporting_tags[classname] = fmt_tag
    union_type = " | ".join(supporting_tags.keys())
    supporting_types = "\n\n".join(supporting_tags.values())
    return union_type, supporting_types


def _generate_pydantic_model_json_response_schema(
    unconstrained_cls: type[T], *, field_cons: FieldCons
) -> str:
    """Using the given unconstrained Pydantic model, generate a constrained Pydantic model schema for a JSON response.

    For a class to be unconstrained means that it still contains placeholders that need to be replaced with concrete types.
    """

    constrained_cls, supporting_discriminated_unions = _dynamic_constraint_factory(
        unconstrained_cls, field_cons=field_cons
    )
    return (
        f"\nCPlease complete the `{unconstrained_cls.__name__}` according to the following"
        " pydantic model and discriminated unions:"
        "\n```"
        f"\n{constrained_cls}"
        f"\n\n{"\n\n".join(supporting_discriminated_unions)}"
        "\n```"
        "\nFor each discriminated union, you should provide the appropriate value."
    )


def _case_completions_system_context(
    *,
    output_cls: type[T] = type[CaseMissingFieldsResponse],
    field_cons: dict[CaseField, list[CategoryConstraint]],
) -> str:
    # Concretize the magic strings in the output_cls
    output_schema_prompt = _generate_pydantic_model_json_response_schema(
        output_cls, field_cons=field_cons
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
    field_cons: FieldCons,
):
    """Given a list of cases, fill in the missing fields for each.

    Approach
    --------
    - We might want to batch these


    Extension
    ---------
    - We want full control over this. Meaning, we should be able to specify which fields and constraints we want to complete.
    - Refactor the API to accept a mapping of Case fields to constraints.

    """

    system_context = _case_completions_system_context(
        output_cls=CaseMissingFieldsResponse,
        field_cons=field_cons,
    )
    logger.info("🧠 Starting case completions for {} cases...", len(cases))
    logger.debug("System context: {}", system_context=system_context)

    async def task(case: Case) -> str:
        prompt = f"""Case JSON Object: ```\n{case.model_dump_json()}\n```"""
        with logger.contextualize(case_id=case.id):
            logger.info("🧠 Starting case completion")
            response: dict[str, str] = await retryable_async_openai_call(
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
            logger.info("🧠 Completed case completion")
            return result.model_dump_json()

    tasks = [task(case) for case in cases]

    for coro in asyncio.as_completed(tasks, timeout=120):
        result = await coro
        yield result + "\n"
    logger.info("🧠 Completed all case completions.")
