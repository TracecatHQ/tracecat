"""Integrations with Datadog security monitoring API.

Inputs and outputs are denoised and normalized.
The interface is an opintionated take on the request / responses most relevant for high-fidelity alert management.
This is not a 1-to-1 mapping to the Datadog API.

API reference: https://docs.datadoghq.com/api/latest/security-monitoring
"""

from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


DD_SITE_TO_URL = {
    "us1": "app.datadoghq.com",
    "us3": "us3.datadoghq.com",
    "us2": "us2.datadoghq.com",
    "eu1": "app.datadoghq.eu",
    "ap1": "app.datadoghq.com",
}


@registry.register(description="Get Datadog SIEM security signals.")
def list_security_signals(datadog_site: str = "us1"):
    pass


@registry.register(description="Update Datadog SIEM security signal.")
def update_security_signal(datadog_site: str = "us1"):
    pass


@registry.register(description="List Datadog SIEM detection rules.")
def list_detection_rules(datadog_site: str = "us1"):
    pass
