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
    TEMPORAL__API_KEY__ARN,
    TEMPORAL__CLUSTER_NAMESPACE,
    TEMPORAL__CLUSTER_URL,
    TEMPORAL__CONNECT_RETRIES,
    TEMPORAL__MTLS_CERT__ARN,
    TEMPORAL__MTLS_ENABLED,
)
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.logger import logger

_client: Client | None = None


@dataclass(frozen=True, slots=True)
class TemporalClientCert:
    cert: bytes
    private_key: bytes


async def _retrieve_temporal_client_cert(arn: str) -> TemporalClientCert:
    """Retrieve the client certificate and private key from AWS Secrets Manager."""
    session = aioboto3.Session()
    async with session.client(service_name="secretsmanager") as client:
        response = await client.get_secret_value(SecretId=arn)
        secret = response["SecretString"]
        return TemporalClientCert(
            cert=secret["cert"].encode(),
            private_key=secret["private_key"].encode(),
        )


async def _retrieve_temporal_api_key(arn: str) -> str:
    """Retrieve the Temporal API key from AWS Secrets Manager."""
    session = aioboto3.Session()
    async with session.client(service_name="secretsmanager") as client:
        response = await client.get_secret_value(SecretId=arn)
        return response["SecretString"]


@retry(
    stop=stop_after_attempt(TEMPORAL__CONNECT_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(TemporalError),
    reraise=True,
)
async def connect_to_temporal() -> Client:
    api_key = None
    tls_config = False

    if TEMPORAL__MTLS_ENABLED:
        if not TEMPORAL__MTLS_CERT__ARN:
            raise ValueError(
                "MTLS enabled for Temporal but `TEMPORAL__MTLS_CERT_ARN` is not set"
            )
        client_cert = await _retrieve_temporal_client_cert(arn=TEMPORAL__MTLS_CERT__ARN)
        tls_config = TLSConfig(
            client_cert=client_cert.cert,
            client_private_key=client_cert.private_key,
        )

    if TEMPORAL__API_KEY__ARN:
        api_key = await _retrieve_temporal_api_key(arn=TEMPORAL__API_KEY__ARN)

    client = await Client.connect(
        target_host=TEMPORAL__CLUSTER_URL,
        namespace=TEMPORAL__CLUSTER_NAMESPACE,
        api_key=api_key,
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
