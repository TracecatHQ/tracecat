from __future__ import annotations

import abc
from functools import cached_property
from typing import Any, Literal, Self, TypeVar

from loguru import logger
from pydantic import BaseModel, Field, model_validator

JSONPrimitive = str | int | float | bool | None | dict[str, Any] | list[Any]
JSONObjectOrArray = dict[str, JSONPrimitive] | list[JSONPrimitive]
MessageTemplate = JSONObjectOrArray
Context = dict[str, Any]

_T = TypeVar("_T", bound=JSONObjectOrArray)


def _eval_message_template(obj: _T, context: dict[str, str]) -> _T:
    """Recursively evaluate the template text inside the `obj` JSON obejct."""
    match obj:
        case list():
            return [_eval_message_template(item, context=context) for item in obj]
        case dict():
            res = {}
            for key, value in obj.items():
                if key == "text" and isinstance(value, str):
                    res[key] = value.format(**context)
                else:
                    res[key] = _eval_message_template(value, context=context)
            return res
        case _:
            return obj


class TemplatedMessage(BaseModel):
    vendor: Literal["slack", "teams"] = Field(..., frozen=True)
    """Chat platform vendor."""

    channel: str  # Slack specific for now
    """Channel ID to post the message."""

    text: str | None = None
    """Message text."""

    template: MessageTemplate | None = None
    """Message template, extended with f-string template support."""

    contexts: list[Context] | None = None
    """List of context key-value objects."""

    @cached_property
    def messages(self) -> list[MessageBase]:
        return self.to_messages()

    def to_messages(self) -> list[MessageBase]:
        """Converts the message templates to hydrated messages."""
        cls = MessageBase.class_factory(self.vendor)
        return [
            cls.from_template(template=self.template, text=self.text, context=context)
            for context in self.contexts
        ]

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if not self.text and not self.template:
            raise ValueError("Either text or blocks must be provided")
        return self


class MessageBase(BaseModel, abc.ABC):
    @staticmethod
    def class_factory(vendor: str) -> type[MessageBase]:
        if vendor == "slack":
            return SlackMessage
        raise ValueError(f"Vendor {vendor} is not supported")

    @classmethod
    @abc.abstractmethod
    def from_template(
        cls, template: MessageTemplate, text: str, context: Context
    ) -> Self:
        raise NotImplementedError


class SlackMessage(MessageBase):
    """Hydrated message to post to Slack."""

    text: str | None = None
    """Message text."""

    blocks: list[dict[str, Any]] | None = None
    """Hydrated blocks definition."""

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if not self.text and not self.blocks:
            raise ValueError("Either text or blocks must be provided")
        return self

    @classmethod
    def from_template(
        cls, template: MessageTemplate, text: str, context: Context
    ) -> Self:
        """Maps contexts over the template to create a list of hydrated messages."""
        formatted_text = text.format(**context)
        if len(context) > 120:
            logger.warning("Truncating message text to 120 characters...")
            formatted_text = f"{formatted_text[:120]}..."
        return cls(
            text=formatted_text,
            blocks=_eval_message_template(obj=template, context=context),
        )
