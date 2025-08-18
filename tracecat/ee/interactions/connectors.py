# Shim for EE connectors - requires EE installation
try:
    from tracecat_ee.interactions.connectors import parse_slack_interaction_input
except ImportError as exc:
    raise ImportError(
        "Tracecat Enterprise features are not installed. "
        "Install with extras: `pip install 'tracecat[ee]'`."
    ) from exc

__all__ = ["parse_slack_interaction_input"]
