from typing import Any

from pydantic import BaseModel, Field

from tracecat.dsl.enums import StreamErrorHandlingStrategy
from tracecat.expressions.validation import ExpressionStr


class ScatterArgs(BaseModel):
    collection: Any = Field(..., description="The collection to scatter")


class GatherArgs(BaseModel):
    """Arguments for gather operations"""

    items: ExpressionStr = Field(..., description="The jsonpath to select items from")
    drop_nulls: bool = Field(
        default=False, description="Whether to drop null values from the final result"
    )
    error_strategy: StreamErrorHandlingStrategy = Field(
        default=StreamErrorHandlingStrategy.PARTITION
    )
