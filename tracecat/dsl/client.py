from dataclasses import dataclass

import aioboto3
from temporalio.client import Client
from temporalio.exceptions import TemporalError
from temporalio.service import TLSConfig
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.config import (
    TEMPORAL__CLUSTER_NAMESPACE,
    TEMPORAL__CLUSTER_URL,
    TEMPORAL__CONNECT_RETRIES,
    TEMPORAL__TLS_CERT__ARN,
    TEMPORAL__TLS_ENABLED,
)
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.logger import logger

_client: Client | None = None


@dataclass(frozen=True, slots=True)
class TemporalClientCert:
    cert: bytes
    private_key: bytes


async def _retrieve_client_cert(arn: str) -> TemporalClientCert:
    """Retrieve the client certificate and private key from AWS Secrets Manager."""
    session = aioboto3.session.get_session()
    async with session.client(service_name="secretsmanager") as client:
        response = await client.get_secret_value(SecretId=arn)
        secret = response["SecretString"]
        return TemporalClientCert(
            cert=secret["cert"].encode(),
            private_key=secret["private_key"].encode(),
        )


@retry(
    stop=stop_after_attempt(TEMPORAL__CONNECT_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(TemporalError),
    reraise=True,
)
async def connect_to_temporal() -> Client:
    tls_config = False

    if TEMPORAL__TLS_ENABLED:
        if not TEMPORAL__TLS_CERT__ARN:
            raise ValueError(
                "MTLS enabled for Temporal but `TEMPORAL__TLS_CERT_ARN` is not set"
            )

        logger.info("Retrieving Temporal MTLS client certificate...")
        client_cert = await _retrieve_client_cert(arn=TEMPORAL__TLS_CERT__ARN)
        logger.info("Successfully retrieved Temporal MTLS client certificate")
        tls_config = TLSConfig(
            client_cert=client_cert.cert,
            client_private_key=client_cert.private_key,
        )

    client = await Client.connect(
        target_host=TEMPORAL__CLUSTER_URL,
        namespace=TEMPORAL__CLUSTER_NAMESPACE,
        tls=tls_config,
        data_converter=pydantic_data_converter,
    )
    return client


async def get_temporal_client() -> Client:
    global _client

    if _client is not None:
        return _client

    try:
        logger.info(
            "Connecting to Temporal server...",
            namespace=TEMPORAL__CLUSTER_NAMESPACE,
            url=TEMPORAL__CLUSTER_URL,
        )
        _client = await connect_to_temporal()
        logger.info("Successfully connected to Temporal server")
    except RetryError as e:
        msg = (
            f"Failed to connect to host {TEMPORAL__CLUSTER_URL} using namespace "
            f"{TEMPORAL__CLUSTER_NAMESPACE} after {TEMPORAL__CONNECT_RETRIES} attempts. "
        )
        raise RuntimeError(msg) from e
    else:
        return _client
