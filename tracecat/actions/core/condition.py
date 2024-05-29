"""Core conditional actions."""

import operator
import re
from collections.abc import Callable
from typing import Annotated, Any, Generic, Literal, TypeVar, override

from loguru import logger
from pydantic import BaseModel, Field, TypeAdapter

from tracecat.registry import registry

ComparisonVariant = Literal[
    "less_than",
    "less_than_or_equal_to",
    "greater_than",
    "greater_than_or_equal_to",
    "equal_to",
    "not_equal_to",
]
RegexVariant = Literal[
    "regex_match",
    "regex_not_match",
]

MembershipVariant = Literal[
    "contains",
    "does_not_contain",
]
ConditionSubtype = ComparisonVariant | RegexVariant | MembershipVariant

CONDITION_FUNCTION_TABLE: dict[ConditionSubtype, Callable[..., bool]] = {
    # Comparison
    "less_than": operator.lt,
    "less_than_or_equal_to": operator.le,
    "greater_than": operator.gt,
    "greater_than_or_equal_to": operator.ge,
    "not_equal_to": operator.ne,
    "equal_to": operator.eq,
    # Regex
    "regex_match": lambda pattern, text: bool(re.match(pattern, text)),
    "regex_not_match": lambda pattern, text: not bool(re.match(pattern, text)),
    # Membership
    "contains": lambda item, container: item in container,
    "does_not_contain": lambda item, container: item not in container,
}


class _Rule(BaseModel):
    type: str
    variant: ConditionSubtype

    def evaluate(self) -> bool:
        raise NotImplementedError


T = TypeVar("T", str, int, float, bool)


class ComparisonRule(_Rule, Generic[T]):
    type: Literal["compare"] = Field(default="compare", frozen=True)
    variant: ComparisonVariant
    lhs: T = Field(..., description="The left-hand side of the comparison")
    rhs: T = Field(..., description="The right-hand side of the comparison")

    @override
    def evaluate(self) -> bool:
        return CONDITION_FUNCTION_TABLE[self.variant](self.lhs, self.rhs)


class RegexRule(_Rule):
    type: Literal["regex"] = Field(default="regex", frozen=True)
    variant: RegexVariant
    pattern: str = Field(..., description="The regex pattern to match")
    text: str

    @override
    def evaluate(self) -> bool:
        return CONDITION_FUNCTION_TABLE[self.variant](self.pattern, self.text)


class MembershipRule(_Rule, Generic[T]):
    type: Literal["membership"] = Field(default="membership", frozen=True)
    variant: MembershipVariant
    item: T
    container: list[T]

    @override
    def evaluate(self) -> bool:
        return CONDITION_FUNCTION_TABLE[self.variant](self.item, self.container)


ConditionVariant = ComparisonRule[T] | RegexRule | MembershipRule[T]
AnnotatedConditionVariant = Annotated[ConditionVariant, Field(discriminator="type")]
ConditionValidator: TypeAdapter[ConditionVariant] = TypeAdapter(
    AnnotatedConditionVariant
)


@registry.register(
    namespace="core.condition",
    version="0.1.0",
    description="Perform a conditional rule evaluation.",
)
async def condition(
    # NOTE: This arrives as a dictionary becaused we called `model_dump` on the ConditionAction instance.
    condition_rules: dict[str, Any],
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a conditional action."""
    logger.debug("Perform conditional rules action", rules=condition_rules)
    rule = ConditionValidator.validate_python(condition_rules)
    rule_match = rule.evaluate()
    return {
        "output": "true" if rule_match else "false",  # Explicitly convert to string
        "output_type": "bool",
        "__should_continue__": rule_match,
    }
