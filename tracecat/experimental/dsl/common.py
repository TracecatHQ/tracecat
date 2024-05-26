from temporalio.client import Client

from tracecat import config
from tracecat.experimental.dsl._converter import pydantic_data_converter


async def get_temporal_client() -> Client:
    return await Client.connect(
        config.TEMPORAL__CLUSTER_URL, data_converter=pydantic_data_converter
    )
