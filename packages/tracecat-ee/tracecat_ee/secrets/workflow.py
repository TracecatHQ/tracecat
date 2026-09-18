"""Check saved references using the executor's AWS workload identity."""

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from tracecat.secrets.enums import AwsSecretResolutionErrorCode
    from tracecat.secrets.schemas import (
        SecretReferenceCheckRequest,
        SecretReferenceCheckResult,
    )
    from tracecat.tiers.entitlements import check_entitlement
    from tracecat.tiers.enums import Entitlement
    from tracecat_ee.secrets.service import ExternalSecretsService


@activity.defn
async def check_secret_reference_activity(
    request: SecretReferenceCheckRequest,
) -> SecretReferenceCheckResult:
    """Resolve in the executor; never put remote values or exceptions in history."""
    try:
        async with ExternalSecretsService.with_session(role=request.role) as service:
            await check_entitlement(
                service.session, request.role, Entitlement.EXTERNAL_SECRET_STORES
            )
            secret = await service.get_secret(request.secret_id)
            return await service.check_aws_secret_reference(secret)
    except Exception:
        # Unexpected provider/DB failures must not serialize their details to Temporal.
        pass
    return SecretReferenceCheckResult(
        ok=False,
        error_code=AwsSecretResolutionErrorCode.UNKNOWN,
        message="Reference check could not complete. Try again.",
    )


@workflow.defn
class SecretReferenceCheckWorkflow:
    """Run a bounded check on the same worker as runtime secret resolution."""

    @workflow.run
    async def run(
        self, request: SecretReferenceCheckRequest
    ) -> SecretReferenceCheckResult:
        return await workflow.execute_activity(
            check_secret_reference_activity,
            request,
            start_to_close_timeout=timedelta(seconds=45),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
