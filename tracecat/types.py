from __future__ import annotations

from pydantic import BaseModel, Field


class TemplatedField(BaseModel):
    json_path: str = Field(
        pattern=r"^(\.?[a-zA-Z0-9_\-]+)+$",
        description="A JSON path to the field to be replaced.",
    )
    field_type: str
