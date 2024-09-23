from collections.abc import Mapping
from typing import Any, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from tracecat_registry import RegistrySecret

ArgsT = TypeVar("ArgsT", bound=Mapping[str, Any])
ArgsClsT = TypeVar("ArgsClsT", bound=type[BaseModel])


class RegisteredUDFMetadata(TypedDict, total=False):
    """Metadata for a registered UDF."""

    is_template: bool
    default_title: str | None
    display_group: str | None
    include_in_schema: bool
    origin: str


class RegisteredUDFRead(BaseModel):
    """API read model for a registered UDF."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    key: str
    description: str
    namespace: str
    version: str | None = None
    secrets: list[RegistrySecret] | None = None
    args_docs: dict[str, str] = Field(default_factory=dict)
    metadata: RegisteredUDFMetadata = Field(default_factory=dict)


class RunActionParams(BaseModel):
    """Arguments for a UDF."""

    args: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
