from __future__ import annotations

import uuid
from typing import Any, ClassVar, Self, cast
from uuid import UUID

from pydantic_core import CoreSchema, core_schema

from tracecat import base62
from tracecat.logger import logger


def id_to_short(id: UUID, prefix: str) -> str:
    """Convert a workflow ID to a string with consistent length.

    Args:
        id: UUID to convert
        prefix: Prefix to add to the encoded string

    Returns:
        str: Prefixed base62 encoded string, padded to 22 characters
    """
    suffix = base62.b62encode(id.int)
    # Pad to 22 characters which is the maximum length needed for a UUID
    padded_suffix = suffix.zfill(22)
    return f"{prefix}{padded_suffix}"


def id_from_short(short_id: str, prefix: str) -> UUID:
    """Convert a workflow ID string to a UUID."""
    if not short_id.startswith(prefix):
        raise ValueError("Invalid short ID string")
    prefix_len = len(prefix)
    suffix = short_id[prefix_len:]
    return UUID(int=base62.b62decode(suffix))


class TracecatUUID[ShortID: str](UUID):
    """Base class for prefixed Tracecat UUIDs.

    This class serves as a base for specialized UUIDs that have specific prefixes
    for different types of resources (workflows, nodes, etc.).

    Generic Args:
        ShortIDType: Type of the short ID representation, must be str or subclass
    """

    prefix: ClassVar[str]
    legacy_prefix: ClassVar[str | None] = None

    def __init__(self, *args, **kwargs) -> None:
        """Initialize a TracecatUUID.

        Args:
            *args: Positional arguments passed to UUID constructor
            **kwargs: Keyword arguments passed to UUID constructor

        Raises:
            ValueError: If the prefix is not set by the subclass
        """
        if not hasattr(self, "prefix"):
            raise ValueError(f"{self.__class__.__name__} requires a prefix to be set")
        super().__init__(*args, **kwargs)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: Any,
    ) -> CoreSchema:
        """Generate Pydantic core schema for validation and serialization."""

        def validate_from_str(value: str) -> Self:
            logger.info("validate_from_str", value=value)
            return cls.new(value)

        def serializer(x: Self) -> str:
            logger.info("serializer", x=x)
            return str(x)

        return core_schema.json_or_python_schema(
            json_schema=core_schema.union_schema(
                [
                    core_schema.no_info_plain_validator_function(validate_from_str),
                ]
            ),
            python_schema=core_schema.union_schema(
                [
                    core_schema.is_instance_schema(cls),
                    core_schema.no_info_plain_validator_function(validate_from_str),
                ]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serializer,
                return_schema=core_schema.str_schema(),
                when_used="json",
            ),
        )

    def short(self) -> ShortID:
        """Convert the UUID to a shortened, prefixed string representation.

        Returns:
            ShortIDType: The shortened ID string with prefix
        """
        return id_to_short(self, self.prefix)  # type: ignore

    @classmethod
    def from_short(cls, short_id: ShortID) -> Self:
        """Create a TracecatUUID instance from a shortened ID string.

        Args:
            short_id: The shortened ID string with prefix

        Returns:
            TracecatUUID[ShortIDType]: A new instance created from the short ID

        Raises:
            ValueError: If the short_id has an invalid prefix
        """
        if not short_id.startswith(cls.prefix):
            raise ValueError(
                f"Invalid prefix '{cls.prefix}' for {cls.__name__}, expected '{cls.prefix}'"
            )

        uuid_obj = id_from_short(short_id, prefix=cls.prefix)
        return cls(int=uuid_obj.int)

    @classmethod
    def make_short(cls, uuid: UUID) -> ShortID:
        return cast(ShortID, id_to_short(uuid, cls.prefix))

    @classmethod
    def from_uuid(cls, uuid: UUID) -> Self:
        return cls(int=uuid.int)

    @classmethod
    def new_uuid4(cls) -> Self:
        return cls.from_uuid(uuid.uuid4())

    @classmethod
    def new(cls, id: Any) -> Self:
        """Coerce an ID into an instance of TracecatUUID. Handles legacy ids."""
        if isinstance(id, cls):
            return id
        match id:
            case UUID():
                # This is a full UUID
                return cls.from_uuid(id)
            case str() if id.startswith(cls.prefix):
                # This is a short ID
                return cls.from_short(cast(ShortID, id))
            case str() if cls.legacy_prefix and id.startswith(cls.legacy_prefix):
                return cls.from_legacy(id)
            case str():
                # This is a full UUID string
                return cls.from_uuid(UUID(id))
            case _:
                raise ValueError(f"Invalid {cls.__name__} ID: {id}")

    @classmethod
    def from_legacy(cls, id: str) -> Self:
        if cls.legacy_prefix is None:
            raise ValueError(f"Legacy IDs are not supported for {cls.__name__}")
        prefix_len = len(cls.legacy_prefix)
        hex_str = id[prefix_len - 1 :]
        return cls.from_uuid(UUID(hex_str))

    def to_legacy(self) -> str:
        if self.legacy_prefix is None:
            raise ValueError(
                f"Legacy IDs are not supported for {self.__class__.__name__}"
            )
        return f"{self.legacy_prefix}{self.hex}"
