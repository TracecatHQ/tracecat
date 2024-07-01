from temporalio.client import Client, TLSConfig

from tracecat import config
from tracecat.dsl._converter import pydantic_data_converter


async def get_temporal_client() -> Client:
    tls_config = False
    if config.TEMPORAL__TLS_ENABLED:
        tls_config = TLSConfig(
            client_cert=config.TEMPORAL__TLS_CLIENT_CERT,
            client_private_key=config.TEMPORAL__TLS_CLIENT_PRIVATE_KEY,
        )

    return await Client.connect(
        target_host=config.TEMPORAL__CLUSTER_URL,
        namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
        tls=tls_config,
        data_converter=pydantic_data_converter,
    )
