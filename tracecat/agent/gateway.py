"""LiteLLM process harness.

Goal: build a LiteLLM config directly from AgentConfig (no extra abstraction)
and launch the sidecar inside the executor when a model requires it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import yaml
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_delay,
    wait_exponential,
)

from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig
from tracecat.exceptions import TracecatCredentialsError
from tracecat.logger import logger


class ProviderCredentials:
    """Container for provider-specific credentials."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_region: str | None = None,
    ):
        self.api_key = api_key
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.aws_region = aws_region


class LiteLLMProcessConfig:
    """Configuration required to launch a LiteLLM server process."""

    def __init__(
        self,
        *,
        config_path: Path,
        agent_config: AgentConfig,
        credentials: ProviderCredentials,
    ):
        self.config_path = config_path
        self.agent_config = agent_config
        self.credentials = credentials

    def build(self) -> dict:
        """Build the LiteLLM config from AgentConfig using a fixed proxy name."""
        litellm_model = (
            f"{self.agent_config.model_provider}/{self.agent_config.model_name}"
        )
        params: dict[str, str] = {"model": litellm_model}
        if self.agent_config.base_url:
            params["api_base"] = self.agent_config.base_url

        # Add credentials based on provider type
        if self.credentials.api_key:
            params["api_key"] = self.credentials.api_key
        if self.credentials.aws_access_key_id:
            params["aws_access_key_id"] = self.credentials.aws_access_key_id
        if self.credentials.aws_secret_access_key:
            params["aws_secret_access_key"] = self.credentials.aws_secret_access_key
        if self.credentials.aws_region:
            params["aws_region_name"] = self.credentials.aws_region

        return {
            "model_list": [
                {
                    "model_name": "agent",
                    "litellm_params": params,
                }
            ],
            "litellm_settings": {
                "drop_params": True,
            },
            "general_settings": {
                "database_url": None,
            },
        }


class LiteLLMProcess:
    """Wrapper for controlling the LiteLLM subprocess."""

    def __init__(
        self,
        agent_config: AgentConfig,
        host: str = "127.0.0.1",
        port: int = 4000,
        use_workspace_credentials: bool = False,
    ):
        self._proc: subprocess.Popen[bytes] | None = None
        self.agent_config = agent_config
        self.host = host
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"
        self._use_workspace_credentials = use_workspace_credentials

    async def _fetch_credentials(self) -> ProviderCredentials:
        """Fetch credentials for model provider based on credential scope."""

        async with AgentManagementService.with_session() as svc:
            if self._use_workspace_credentials:
                credentials = await svc.get_workspace_provider_credentials(
                    self.agent_config.model_provider
                )
            else:
                credentials = await svc.get_provider_credentials(
                    self.agent_config.model_provider
                )

        if not credentials:
            raise TracecatCredentialsError(
                f"No credentials found for provider '{self.agent_config.model_provider}'."
            )

        provider = self.agent_config.model_provider

        if provider == "bedrock":
            # Bedrock supports either bearer token or AWS access keys
            bearer_token = credentials.get("AWS_BEARER_TOKEN_BEDROCK")
            if bearer_token:
                return ProviderCredentials(api_key=bearer_token)
            # Fall back to AWS access keys
            aws_access_key_id = credentials.get("AWS_ACCESS_KEY_ID")
            aws_secret_access_key = credentials.get("AWS_SECRET_ACCESS_KEY")
            aws_region = credentials.get("AWS_REGION")
            if not (aws_access_key_id and aws_secret_access_key and aws_region):
                raise TracecatCredentialsError(
                    "Bedrock requires either AWS_BEARER_TOKEN_BEDROCK or "
                    "all of AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_REGION."
                )
            return ProviderCredentials(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_region=aws_region,
            )

        # Standard API key providers
        key_mapping = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "custom-model-provider": "CUSTOM_MODEL_PROVIDER_API_KEY",
        }
        key_name = key_mapping.get(provider)
        api_key = credentials.get(key_name) if key_name else None
        return ProviderCredentials(api_key=api_key)

    async def start(self, *, timeout: float = 10.0) -> None:
        """Start the LiteLLM server process.

        Fetches API credentials JIT and starts the process.
        """
        credentials = await self._fetch_credentials()

        config = LiteLLMProcessConfig(
            config_path=Path("/tmp/litellm.yaml"),
            agent_config=self.agent_config,
            credentials=credentials,
        )
        cfg = config.build()
        config.config_path.write_text(yaml.safe_dump(cfg))

        cmd = [
            "litellm",
            "--config",
            str(config.config_path),
            "--port",
            str(self.port),
        ]

        logger.info(
            "Starting LiteLLM",
            host=self.host,
            port=self.port,
        )

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for health check with exponential backoff
        health_url = f"{self.base_url}/health"

        try:
            async with httpx.AsyncClient() as client:

                @retry(
                    stop=stop_after_delay(timeout),
                    wait=wait_exponential(multiplier=0.1, min=0.05, max=2),
                    retry=retry_if_exception_type(
                        (httpx.RequestError, httpx.HTTPStatusError)
                    ),
                    reraise=True,
                )
                async def check_health() -> None:
                    if self._proc and self._proc.poll() is not None:
                        stdout, stderr = self._proc.communicate()
                        raise RuntimeError(
                            f"LiteLLM process exited early: {stdout.decode()} {stderr.decode()}"
                        )
                    response = await client.get(health_url)
                    response.raise_for_status()

                await check_health()
            logger.info("LiteLLM ready", base_url=self.base_url)
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        """Terminate the LiteLLM server process."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait()
        self._proc = None
