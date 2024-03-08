from __future__ import annotations

import operator
import re
from collections.abc import Callable
from typing import Annotated, Generic, Literal, TypeVar, override

from pydantic import BaseModel, Field

ComparisonSubtype = Literal[
    "less_than",
    "less_than_or_equal_to",
    "greater_than",
    "greater_than_or_equal_to",
    "equal_to",
    "not_equal_to",
]
RegexSubtype = Literal[
    "regex_match",
    "regex_not_match",
]

MembershipSubtype = Literal[
    "contains",
    "does_not_contain",
]
ConditionSubtype = ComparisonSubtype | RegexSubtype | MembershipSubtype

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
    type: Literal["comparison", "regex", "membership"]
    subtype: ConditionSubtype

    def evaluate(self) -> bool:
        raise NotImplementedError


T = TypeVar("T", str, int, float, bool)


class ComparisonRule(_Rule, Generic[T]):
    type: Literal["comparison"] = Field(default="comparison", frozen=True)
    subtype: ComparisonSubtype
    lhs: T = Field(..., description="The left-hand side of the comparison")
    rhs: T = Field(..., description="The right-hand side of the comparison")

    @override
    def evaluate(self) -> bool:
        return CONDITION_FUNCTION_TABLE[self.subtype](self.lhs, self.rhs)


class RegexRule(_Rule):
    type: Literal["regex"] = Field(default="regex", frozen=True)
    subtype: RegexSubtype
    pattern: str = Field(..., description="The regex pattern to match")
    text: str

    @override
    def evaluate(self) -> bool:
        return CONDITION_FUNCTION_TABLE[self.subtype](self.pattern, self.text)


class MembershipRule(_Rule, Generic[T]):
    type: Literal["membership"] = Field(default="membership", frozen=True)
    subtype: MembershipSubtype
    item: T
    container: list[T]

    @override
    def evaluate(self) -> bool:
        return CONDITION_FUNCTION_TABLE[self.subtype](self.item, self.container)


ConditionRuleVariant = Annotated[
    ComparisonRule[T] | RegexRule | MembershipRule[T], Field(discriminator="type")
]
