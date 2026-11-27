"""Confirm an SNS subscription with the ``ConfirmSubscription`` API.

``SubscribeURL`` is never fetched. A subscription is confirmed through the
``ConfirmSubscription`` API with the token, in the pinned topic's region.

Reference:
- ConfirmSubscription:
  https://docs.aws.amazon.com/sns/latest/api/API_ConfirmSubscription.html
"""

from __future__ import annotations

import aioboto3
from aiobotocore.config import AioConfig
from botocore import UNSIGNED
from botocore.exceptions import BotoCoreError, ClientError

from tracecat.logger import logger
from tracecat.triggers.ingest.aws_sns.types import SnsTopic
from tracecat.triggers.ingest.types import IngestError, IngestErrorCode

# ConfirmSubscription errors that mean the token or topic is wrong, so a retry
# cannot succeed. Others (throttling, AWS errors) are worth a retry.
_FINAL_CONFIRM_ERRORS = frozenset(
    {"InvalidParameter", "NotFound", "AuthorizationError", "InvalidParameterValue"}
)
_CONFIRM_CLIENT_CONFIG = AioConfig(
    # The token proves the right to confirm; no Tracecat credentials are used,
    # the same as the unsigned SubscribeURL request.
    signature_version=UNSIGNED,
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 2, "mode": "standard"},
)


async def confirm_subscription(*, topic: SnsTopic, token: str) -> str:
    """Confirm a pending subscription with the ``ConfirmSubscription`` API.

    Returns:
        The subscription ARN.

    Raises:
        IngestError: ``CONFIRMATION_REJECTED`` if SNS refused the token,
            ``UNAVAILABLE`` if the call failed in a way a retry may fix.
    """
    session = aioboto3.Session()
    try:
        async with session.client(
            "sns", region_name=topic.region, config=_CONFIRM_CLIENT_CONFIG
        ) as sns:
            # No AuthenticateOnUnsubscribe: it needs a signed request, and
            # this one is unsigned. The subscription ARN is shown only to
            # callers who may change the source.
            response = await sns.confirm_subscription(TopicArn=topic.arn, Token=token)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.warning(
            "SNS refused a subscription confirmation", topic_arn=topic.arn, code=code
        )
        if code in _FINAL_CONFIRM_ERRORS:
            raise IngestError(
                IngestErrorCode.CONFIRMATION_REJECTED, "SNS refused the confirmation"
            ) from None
        raise IngestError(
            IngestErrorCode.UNAVAILABLE, "SNS could not confirm the subscription"
        ) from None
    except BotoCoreError as e:
        logger.warning(
            "SNS confirmation call failed",
            topic_arn=topic.arn,
            error_type=type(e).__name__,
        )
        raise IngestError(
            IngestErrorCode.UNAVAILABLE, "SNS could not confirm the subscription"
        ) from None
    return response["SubscriptionArn"]
