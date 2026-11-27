"""Wire models for SNS deliveries: the signed envelope and its message types.

Reference:
- SNS message and JSON formats:
  https://docs.aws.amazon.com/sns/latest/dg/sns-message-and-json-formats.html
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SnsMessageType(StrEnum):
    NOTIFICATION = "Notification"
    SUBSCRIPTION_CONFIRMATION = "SubscriptionConfirmation"
    UNSUBSCRIBE_CONFIRMATION = "UnsubscribeConfirmation"


class SnsEnvelope(BaseModel):
    """A signed SNS message as POSTed to an HTTPS subscription.

    ``timestamp`` stays the raw string: the signature covers it as sent.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: SnsMessageType = Field(alias="Type")
    message_id: str = Field(alias="MessageId", min_length=1, max_length=128)
    topic_arn: str = Field(alias="TopicArn", min_length=1, max_length=1024)
    message: str = Field(alias="Message")
    timestamp: str = Field(alias="Timestamp", min_length=1, max_length=64)
    signature_version: Literal["1", "2"] = Field(alias="SignatureVersion")
    signature: str = Field(alias="Signature", min_length=1, max_length=4096)
    signing_cert_url: str = Field(alias="SigningCertURL", min_length=1, max_length=2048)
    subject: str | None = Field(default=None, alias="Subject", max_length=1024)
    # Confirmation messages only. ``subscribe_url`` is signed, so it is read
    # for the signature, and never fetched.
    token: str | None = Field(default=None, alias="Token", max_length=2048)
    subscribe_url: str | None = Field(
        default=None, alias="SubscribeURL", max_length=4096
    )

    @model_validator(mode="after")
    def _confirmation_fields(self) -> Self:
        if self.type != SnsMessageType.NOTIFICATION and (
            not self.token or not self.subscribe_url
        ):
            raise ValueError("Confirmation messages carry Token and SubscribeURL")
        return self

    def string_to_sign(self) -> str:
        """The canonical text SNS signed: ``name\\nvalue\\n`` per signed field."""
        if self.type == SnsMessageType.NOTIFICATION:
            fields: list[tuple[str, str | None]] = [
                ("Message", self.message),
                ("MessageId", self.message_id),
                ("Subject", self.subject),
                ("Timestamp", self.timestamp),
                ("TopicArn", self.topic_arn),
                ("Type", self.type.value),
            ]
        else:
            fields = [
                ("Message", self.message),
                ("MessageId", self.message_id),
                ("SubscribeURL", self.subscribe_url),
                ("Timestamp", self.timestamp),
                ("Token", self.token),
                ("TopicArn", self.topic_arn),
                ("Type", self.type.value),
            ]
        return "".join(
            f"{name}\n{value}\n" for name, value in fields if value is not None
        )
