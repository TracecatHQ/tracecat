"""Domain types for SNS ingest."""

from __future__ import annotations

from dataclasses import dataclass


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
