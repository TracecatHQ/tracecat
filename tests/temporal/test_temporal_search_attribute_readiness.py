import pytest
from temporalio.testing import WorkflowEnvironment

from tracecat.temporal.search_attributes import ensure_error_owner_search_attribute
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal]


@pytest.mark.anyio
async def test_error_owner_search_attribute_readiness_rejects_missing_attribute() -> (
    None
):
    async with await WorkflowEnvironment.start_local(
        dev_server_log_level="error",
    ) as env:
        with pytest.raises(RuntimeError, match="TracecatErrorOwner"):
            await ensure_error_owner_search_attribute(env.client)


@pytest.mark.anyio
async def test_error_owner_search_attribute_readiness_accepts_registered_attribute() -> (
    None
):
    async with await WorkflowEnvironment.start_local(
        search_attributes=[TemporalSearchAttr.ERROR_OWNER.key],
        dev_server_log_level="error",
    ) as env:
        await ensure_error_owner_search_attribute(env.client)
