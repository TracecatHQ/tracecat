from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator

from tracecat.db.models import WorkspaceVariable
from tracecat.identifiers import VariableID, WorkspaceID
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT

VariableName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a variable name. e.g. 'api_config'"""

VariableKey = Annotated[
    str, StringConstraints(pattern=r"[a-zA-Z0-9_]+", min_length=1, max_length=255)
]
"""Validator for a variable key. e.g. 'base_url'"""


class VariableKeyValue(BaseModel):
    key: VariableKey
    value: Any


class VariableCreate(BaseModel):
    name: VariableName = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=0, max_length=255)
    values: dict[VariableKey, Any] = Field(..., min_length=1, max_length=1000)
    tags: dict[str, str] | None = None
    environment: str = DEFAULT_SECRETS_ENVIRONMENT

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: dict[VariableKey, Any]) -> dict[VariableKey, Any]:
        if not values:
            raise ValueError("Values cannot be empty")
        return values


class VariableUpdate(BaseModel):
    name: VariableName | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=0, max_length=255)
    values: dict[VariableKey, Any] | None = Field(
        default=None, min_length=1, max_length=1000
    )
    tags: dict[str, str] | None = Field(default=None, min_length=0, max_length=1000)
    environment: str | None = Field(default=None, min_length=1, max_length=100)


class VariableSearch(BaseModel):
    names: set[VariableName] | None = None
    ids: set[VariableID] | None = None
    environment: str | None = None


class VariableReadMinimal(BaseModel):
    id: VariableID
    name: VariableName
    description: str | None
    values: dict[str, Any]
    environment: str


class VariableRead(BaseModel):
    id: VariableID
    name: VariableName
    description: str | None
    values: dict[str, Any]
    environment: str
    tags: dict[str, str] | None
    workspace_id: WorkspaceID
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_database(obj: WorkspaceVariable) -> VariableRead:
        return VariableRead(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            values=obj.values,
            environment=obj.environment,
            tags=obj.tags,
            workspace_id=obj.workspace_id,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )
