"""LiteLLM process harness placeholder.

Goal: build a LiteLLM config directly from AgentConfig (no extra abstraction)
and launch the sidecar inside the executor when a model requires it.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx
import yaml

from tracecat.agent.types import AgentConfig
from tracecat.logger import logger


class LiteLLMProcessConfig:
    """Configuration required to launch a LiteLLM server process."""

    def __init__(
        self,
        *,
        config_path: Path,
        agent_config: AgentConfig,
    ):
        self.config_path = config_path
        self.agent_config = agent_config

    def build(self) -> dict:
        """Build the LiteLLM config from AgentConfig using a fixed proxy name."""

        litellm_model = self.agent_config.model_name

        # TODO: support for bedrock/vertex and other models
        if self.agent_config.model_provider and "/" not in self.agent_config.model_name:
            litellm_model = (
                f"{self.agent_config.model_provider}/{self.agent_config.model_name}"
            )
        params: dict[str, str] = {"model": litellm_model}
        if self.agent_config.base_url:
            params["api_base"] = self.agent_config.base_url
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
    """Placeholder wrapper for controlling the LiteLLM subprocess."""

    _instances: dict[str, LiteLLMProcess] = {}
    _next_port: int = 4000

    def __init__(
        self, agent_config: AgentConfig, host: str = "127.0.0.1", port: int = 4000
    ):
        self._proc: subprocess.Popen[bytes] | None = None
        self.agent_config = agent_config
        self.host = host
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"

    @classmethod
    def get_or_create(cls, agent_config: AgentConfig) -> LiteLLMProcess:
        """Get or create a LiteLLM process for the given config.

        Caches processes by config to enable reuse across agent runs.
        Each unique config gets its own process on a unique port.
        """
        key = cls._config_key(agent_config)

        if key not in cls._instances:
            port = cls._next_port
            cls._next_port += 1
            logger.info(
                "Executor: creating new LiteLLM process",
                config_key=key,
                port=port,
            )
            cls._instances[key] = cls(agent_config, port=port)

        return cls._instances[key]

    @staticmethod
    def _config_key(config: AgentConfig) -> str:
        """Generate a cache key from the agent config.

        Includes model_provider, model_name, and base_url as these
        are the fields that affect the LiteLLM configuration.
        """
        provider = config.model_provider or ""
        model = config.model_name or ""
        base_url = config.base_url or ""
        return f"{provider}:{model}:{base_url}"

    async def ensure_started(
        self,
        *,
        timeout: float = 30.0,
    ) -> None:
        """Ensure the LiteLLM server process is running and healthy.

        Idempotent: safe to call multiple times. If the process is already running
        and healthy, returns immediately. If the process died, restarts it.
        """

        # Check if process is already running and healthy
        if self._proc and self._proc.poll() is None:
            # Verify the server is still healthy
            health_url = f"{self.base_url}/health"
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(health_url, timeout=5.0)
                    if response.status_code == 200:
                        logger.debug(
                            "Executor: litellm_proc already running",
                            litellm_proc=self.base_url,
                        )
                        return
            except httpx.RequestError:
                # Process exists but isn't healthy, fall through to restart
                logger.warning(
                    "Executor: litellm_proc exists but not healthy, restarting"
                )
                self.stop()
        elif self._proc:
            # Process reference exists but process is dead, clean it up
            logger.warning("Executor: litellm_proc died, restarting")
            self._proc = None
        config = LiteLLMProcessConfig(
            config_path=Path("/tmp/litellm.yaml"), agent_config=self.agent_config
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
            "Executor: starting LiteLLM",
            host=self.host,
            port=self.port,
        )

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.time() + timeout
        health_url = f"{self.base_url}/health"

        async with httpx.AsyncClient() as client:
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    stdout, stderr = self._proc.communicate()
                    raise RuntimeError(
                        f"LiteLLM process exited early: {stdout.decode()} {stderr.decode()}"
                    )
                try:
                    response = await client.get(health_url)
                    if response.status_code == 200:
                        logger.info(
                            "Executor: litellm_proc ready", litellm_proc=self.base_url
                        )
                        return
                except httpx.RequestError:
                    pass
        self.stop()
        raise TimeoutError(f"LiteLLM did not become ready at {health_url}")

    def stop(self) -> None:
        """Terminate the LiteLLM server process and clean up resources."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait()
        self._proc = None
