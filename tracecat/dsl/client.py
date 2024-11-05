from temporalio.client import Client
from temporalio.service import TLSConfig
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat import config
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.logger import logger

_N_RETRIES = 10
_client: Client | None = None


@retry(
    stop=stop_after_attempt(_N_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RuntimeError),
    reraise=True,
)
async def connect_to_temporal() -> Client:
    tls_config = False
    if config.TEMPORAL__TLS_ENABLED:
        if (
            config.TEMPORAL__TLS_CLIENT_CERT is None
            or config.TEMPORAL__TLS_CLIENT_PRIVATE_KEY is None
        ):
            raise RuntimeError(
                "TLS is enabled but no client certificate or private key is provided"
            )
        logger.info("TLS enabled for Temporal")
        tls_config = TLSConfig(
            client_cert=config.TEMPORAL__TLS_CLIENT_CERT.encode(),
            client_private_key=config.TEMPORAL__TLS_CLIENT_PRIVATE_KEY.encode(),
        )

    client = await Client.connect(
        target_host=config.TEMPORAL__CLUSTER_URL,
        namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
        tls=tls_config,
        data_converter=pydantic_data_converter,
    )
    return client


async def get_temporal_client() -> Client:
    global _client

    if _client is not None:
        return _client
    logger.info(f"Connecting to Temporal at {config.TEMPORAL__CLUSTER_URL}")
    try:
        _client = await connect_to_temporal()
        logger.info("Successfully connected to Temporal")
        return _client
    except Exception as e:
        logger.error(
            f"Failed to connect to Temporal after {_N_RETRIES} attempts: {str(e)}"
        )
        raise
