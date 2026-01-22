from typing import Self

from pydantic import BaseModel, ConfigDict, TypeAdapter


class Schema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        arbitrary_types_allowed=True,
    )

    @classmethod
    def list_adapter(cls) -> TypeAdapter[list[Self]]:
        return TypeAdapter(list[cls], config=cls.model_config)  # type: ignore[invalid-type-form]
