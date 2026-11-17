from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, TypeAdapter


class Schema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        arbitrary_types_allowed=True,
    )

    @classmethod
    def list_adapter(cls) -> TypeAdapter[list[Self]]:
        return TypeAdapter(list[cls], config=cls.model_config)  # type: ignore[invalid-type-form]


class Variant(BaseModel):
    """A union member whose discriminator always serializes, even at its default.

    The worker's payload converter dumps with ``exclude_unset``, which drops
    defaulted fields, including a defaulted discriminator. Name the
    discriminator once on the union's base,
    ``class Base(Variant, discriminator="type")``; every subclass below it must
    declare that field. A subclass that overrides ``model_post_init`` must call
    super.
    """

    __discriminator: ClassVar[str]  # name-mangled: private to Variant

    def __init_subclass__(cls, discriminator: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if discriminator is not None:
            cls.__discriminator = discriminator
        elif not hasattr(cls, "_Variant__discriminator"):
            raise TypeError(
                f"{cls.__name__} needs a discriminator: "
                f'class {cls.__name__}(Variant, discriminator="...")'
            )

    @classmethod
    def __pydantic_init_subclass__(
        cls, discriminator: str | None = None, **kwargs: Any
    ) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # The union's base names the discriminator; every member below it
        # declares the field.
        if discriminator is None and cls.__discriminator not in cls.model_fields:
            raise TypeError(
                f"{cls.__name__} has no discriminator field {cls.__discriminator!r}"
            )

    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        self.model_fields_set.add(self.__discriminator)
