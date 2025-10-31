"""OpenAI Codex integration.

Sandbox options:
- [x] Modal sandbox
- [ ] AWS Lambda (enterprise only)

Supports GitHub repo download and in-sandbox MCP server via Modal tunnel (https://modal.com/docs/guide/tunnels) and uvx.
https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#download-a-repository-archive-tar
"""

# Must be imported directly to preserve the udf metadata
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.logger import logger

if is_feature_enabled(FeatureFlag.AGENT_SANDBOX):
    logger.info(
        "Agent sandbox feature flag is enabled. Enabling OpenAI Codex integration."
    )
    from tracecat_ee.sandbox.openai_codex import codex
else:
    codex = None
    logger.info(
        "Agent sandbox feature flag is not enabled. Skipping OpenAI Codex integration."
    )
