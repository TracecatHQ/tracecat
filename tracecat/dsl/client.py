import base64

import aioboto3
from temporalio.client import Client, Plugin
from temporalio.exceptions import TemporalError
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat import config
from tracecat.dsl._converter import get_data_converter
from tracecat.logger import logger

_client: Client | None = None


async def _retrieve_temporal_api_key(arn: str) -> str:
    """Retrieve the Temporal API key from AWS Secrets Manager."""
    session = aioboto3.Session()
    async with session.client(service_name="secretsmanager") as client:
        response = await client.get_secret_value(SecretId=arn)
        secret_string = response.get("SecretString")
        if not secret_string and response.get("SecretBinary"):
            secret_string = base64.b64decode(response["SecretBinary"]).decode("utf-8")
        if not secret_string:
            raise RuntimeError("Temporal API key secret is empty")
        return secret_string


@retry(
    stop=stop_after_attempt(config.TEMPORAL__CONNECT_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=120),  # Up to 2 minutes
    retry=retry_if_exception_type((TemporalError, RuntimeError)),
    reraise=True,
)
async def connect_to_temporal(plugins: list[Plugin] | None = None) -> Client:
    api_key = None
    tls_config = False
    rpc_metadata = {}

    if config.TEMPORAL__API_KEY__ARN:
        api_key = await _retrieve_temporal_api_key(arn=config.TEMPORAL__API_KEY__ARN)
    elif config.TEMPORAL__API_KEY:
        api_key = config.TEMPORAL__API_KEY

    if api_key is not None:
        tls_config = True
        rpc_metadata["temporal-namespace"] = config.TEMPORAL__CLUSTER_NAMESPACE

    runtime = None
    # TODO: fix https://github.com/prometheus/client_python/issues/155
    if config.TEMPORAL__METRICS_PORT:
        logger.info(
            "Initializing Prometheus runtime", port=config.TEMPORAL__METRICS_PORT
        )
        try:
            runtime = init_runtime_with_prometheus(
                port=int(config.TEMPORAL__METRICS_PORT)
            )
        except Exception as e:
            logger.warning("Failed to initialize Prometheus runtime", error=e)
    client = await Client.connect(
        target_host=config.TEMPORAL__CLUSTER_URL,
        namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
        rpc_metadata=rpc_metadata,
        api_key=api_key,
        tls=tls_config,
        data_converter=get_data_converter(
            compression_enabled=config.TRACECAT__CONTEXT_COMPRESSION_ENABLED
        ),
        runtime=runtime,
        plugins=plugins or [],
    )
    return client


async def get_temporal_client(plugins: list[Plugin] | None = None) -> Client:
    global _client

    if _client is not None:
        return _client

    try:
        logger.info(
            "Connecting to Temporal server...",
            namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
            url=config.TEMPORAL__CLUSTER_URL,
        )
        _client = await connect_to_temporal(plugins=plugins)
        logger.info("Successfully connected to Temporal server")
    except RetryError as e:
        msg = (
            f"Failed to connect to host {config.TEMPORAL__CLUSTER_URL} using namespace "
            f"{config.TEMPORAL__CLUSTER_NAMESPACE} after "
            f"{config.TEMPORAL__CONNECT_RETRIES} attempts. "
        )
        raise RuntimeError(msg) from e
    else:
        return _client


def init_runtime_with_prometheus(port: int) -> Runtime:
    # Create runtime for use with Prometheus metrics
    return Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(bind_address=f"0.0.0.0:{port}")
        )
    )
