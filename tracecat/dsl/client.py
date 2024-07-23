from temporalio.client import Client, TLSConfig

from tracecat import config
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.logging import logger


async def get_temporal_client() -> Client:
    logger.info("Connecting to Temporal at %s", config.TEMPORAL__CLUSTER_URL)

    tls_config = False
    if config.TEMPORAL__TLS_ENABLED:
        logger.info("TLS enabled for Temporal")
        tls_config = TLSConfig(
            client_cert=config.TEMPORAL__TLS_CLIENT_CERT,
            client_private_key=config.TEMPORAL__TLS_CLIENT_PRIVATE_KEY,
        )

    client = await Client.connect(
        target_host=config.TEMPORAL__CLUSTER_URL,
        namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
        tls=tls_config,
        data_converter=pydantic_data_converter,
    )
    return client
