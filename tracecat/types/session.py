from typing import Literal

from pydantic import BaseModel


class Role(BaseModel):
    variant: Literal["user", "service"]
    id: str
