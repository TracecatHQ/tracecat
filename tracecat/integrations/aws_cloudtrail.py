"""Native integration to query AWS CloudTrail logs in S3.

Optional secrets: `aws-cloudtrail-s3` secret with keys `

Note: this integration DOES NOT support IAM credential based authentication.
Secrets are only used to obscure potentially sensitive data (account ID, organization ID).
"""

from typing import Literal

from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


AWS_REGION_NAMES = Literal[
    "us-east-1",  # US East (N. Virginia)
    "us-east-2",  # US East (Ohio)
    "us-west-1",  # US West (N. California)
    "us-west-2",  # US West (Oregon)
    "af-south-1",  # Africa (Cape Town)
    "ap-east-1",  # Asia Pacific (Hong Kong)
    "ap-south-1",  # Asia Pacific (Mumbai)
    "ap-northeast-3",  # Asia Pacific (Osaka)
    "ap-northeast-2",  # Asia Pacific (Seoul)
    "ap-southeast-1",  # Asia Pacific (Singapore)
    "ap-southeast-2",  # Asia Pacific (Sydney)
    "ap-northeast-1",  # Asia Pacific (Tokyo)
    "ca-central-1",  # Canada (Central)
    "eu-central-1",  # Europe (Frankfurt)
    "eu-west-1",  # Europe (Ireland)
    "eu-west-2",  # Europe (London)
    "eu-south-1",  # Europe (Milan)
    "eu-west-3",  # Europe (Paris)
    "eu-north-1",  # Europe (Stockholm)
    "me-south-1",  # Middle East (Bahrain)
    "sa-east-1",  # South America (São Paulo)
]


@registry.register(description="Search AWS CloudTrail logs in S3")
def search_aws_cloudtrail(
    start: str,
    end: str,
    account_id: str | None = None,
    regions: AWS_REGION_NAMES | None = None,
    organization_id: str | None = None,
):
    pass
