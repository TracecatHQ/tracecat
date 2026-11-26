"""Domain types for the public ingest endpoints."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from tracecat.exceptions import TracecatException
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.triggers.enums import TriggerStatus


class IngestErrorCode(StrEnum):
    """Why an ingest request was refused. The router maps each to a status.

    SNS retries only on 5xx (and timeouts). Every 4xx is final: SNS drops the
    message, or moves it to the subscription's dead-letter queue.
    """

    PAYLOAD_TOO_LARGE = "payload_too_large"
    MALFORMED = "malformed"
    RAW_DELIVERY = "raw_delivery"
    SOURCE_NOT_FOUND = "source_not_found"
    NOT_ENTITLED = "not_entitled"
    TOPIC_MISMATCH = "topic_mismatch"
    SIGNATURE_INVALID = "signature_invalid"
    STALE_MESSAGE = "stale_message"
    SOURCE_OFFLINE = "source_offline"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


class IngestError(TracecatException):
    """An ingest request that cannot be served. ``code`` selects the response."""

    def __init__(
        self,
        code: IngestErrorCode,
        message: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class SnsIngestSource:
    """What ingest needs to know about an SNS event source."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    organization_id: OrganizationID
    topic_arn: str
    status: TriggerStatus


@dataclass(frozen=True, slots=True)
class SnsTopic:
    """The parts of a pinned topic ARN that ingest uses."""

    arn: str
    partition: str
    region: str
    account: str

    @classmethod
    def parse(cls, arn: str) -> SnsTopic:
        """Split an SNS topic ARN already validated by the source schema.

        Raises:
            ValueError: If the ARN is not an SNS topic ARN.
        """
        match arn.split(":"):
            case ["arn", partition, "sns", region, account, name] if (
                partition and region and account and name
            ):
                return cls(arn=arn, partition=partition, region=region, account=account)
            case _:
                raise ValueError("Not an SNS topic ARN")

    @property
    def sns_host(self) -> str:
        """The SNS API host for the topic's region, which also serves its certificates."""
        suffix = "amazonaws.com.cn" if self.partition == "aws-cn" else "amazonaws.com"
        return f"sns.{self.region}.{suffix}"


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What a handled message did, for the response and the logs."""

    queued: int = 0
    duplicates: int = 0
    ignored: int = 0
    confirmed: bool = False
