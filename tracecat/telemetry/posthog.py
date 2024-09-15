import os

import posthog

from tracecat.logger import logger
from tracecat.telemetry.events import BaseTelemetryEvent


class PosthogClient:
    """
    This is a write-only project API key, so it can only create new events. It can't
    read events or any of your other data stored with PostHog, so it's safe to use in public apps.
    """

    def __init__(self, api_key: str, enabled: bool = True, debug: bool = False):
        self.enabled = enabled
        self.debug = debug

        if self.enabled:
            logger.info(
                "Initializing anonymized telemetry. To disable, set TELEMETRY_ENABLED=false in your environment."
            )
            posthog.project_api_key = api_key
        else:
            posthog.disabled = True

        if self.debug:
            posthog.debug = True

        logger.info(
            f"Posthog telemetry {'enabled' if self.enabled else 'disabled'}, debug mode {'on' if self.debug else 'off'}"
        )

    def capture(self, event: BaseTelemetryEvent):
        if self.enabled:
            posthog.capture(event.event_id, event.event_type, event.properties)


# Initialize the telemetry client with a flag to enable or disable telemetry
telemetry_enabled = os.getenv("TELEMETRY_ENABLED", "true").lower() in ("true", "1")
debug_mode = os.getenv("DEBUG_MODE", "false").lower() in ("true", "1")
telemetry_client = PosthogClient(
    # XXX(SECURITY): This is a write-only project API key, so it can only create new events. It can't
    # read events or any of your other data stored with PostHog, so it's safe to use in public apps.
    api_key="phc_eyGcA9y2Jpm2DLe3D4oSqu2QwOGk8luiAzNGmOCVF2o",  # noqa
    enabled=telemetry_enabled,
)
