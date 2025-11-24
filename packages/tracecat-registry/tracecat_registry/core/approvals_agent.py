from tracecat.feature_flags import is_feature_enabled, FeatureFlag
from tracecat.logger import logger

if is_feature_enabled(FeatureFlag.AGENT_APPROVALS):
    from tracecat_ee.agent.actions import approvals_agent, preset_approvals_agent
else:
    approvals_agent = None
    preset_approvals_agent = None
    logger.info(
        "Agent approvals feature flag is not enabled. Skipping Approval Agent action."
    )
