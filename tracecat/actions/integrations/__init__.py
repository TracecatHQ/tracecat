from collections.abc import Callable

from .cdr import (
    list_defender_cloud_alerts,
    list_guardduty_alerts,
    list_wiz_alerts,
)
from .chat import (
    list_slack_users,
    post_slack_messages,
)
from .edr import (
    list_crowdstrike_alerts,
    list_defender_endpoint_alerts,
    list_sentinelone_alerts,
)
from .enrichment import (
    abuseipdb,
    alienvault,
    hybrid_analysis,
    malwarebazaar,
    pulsedive,
    urlscan,
    virustotal,
)
from .siem import (
    list_datadog_alerts,
    list_elastic_alerts,
)

CDR_CAPABILITIES = {
    "list_alerts": {
        "aws_guardduty": list_guardduty_alerts,
        "microsoft_defender": list_defender_cloud_alerts,
        "wiz": list_wiz_alerts,
    }
}

EDR_CAPABILITIES = {
    "list_alerts": {
        "crowdstrike": list_crowdstrike_alerts,
        "sentinelone": list_sentinelone_alerts,
        "microsoft_defender": list_defender_endpoint_alerts,
    }
}


ENRICHMENT_CAPABILITIES = {
    "analyze_url": {
        "alienvault": alienvault.analyze_url,
        "pulsedive": pulsedive.analyze_url,
        "urlscan": urlscan.analyze_url,
        "virustotal": virustotal.analyze_url,
    },
    "analyze_ip_address": {
        "abuseipdb": abuseipdb.analyze_ip_address,
        "alienvault": alienvault.analyze_ip_address,
        "pulsedive": pulsedive.analyze_ip_address,
        "virustotal": virustotal.analyze_ip_address,
    },
    "analyze_malware_sample": {
        "hybrid_analysis": hybrid_analysis.analyze_malware_sample,
        "malwarebazaar": malwarebazaar.analyze_malware_sample,
        "virustotal": virustotal.analyze_malware_sample,
    },
}

SIEM_CAPABILITIES = {
    "list_alerts": {
        "datadog": list_datadog_alerts,
        "elastic": list_elastic_alerts,
    }
}

CHAT_CAPABILITIES = {
    "post_message": {
        "slack": post_slack_messages,
    },
    "list_users": {
        "slack": list_slack_users,
    },
}


CATEGORY_TO_CAPABILITIES = {
    "cdr": CDR_CAPABILITIES,
    "edr": EDR_CAPABILITIES,
    "siem": SIEM_CAPABILITIES,
    "chat": CHAT_CAPABILITIES,
    "enrichment": ENRICHMENT_CAPABILITIES,
}


def get_capability(
    category: str, capability: str, vendor: str | None = None
) -> Callable:
    try:
        capabilities = CATEGORY_TO_CAPABILITIES[category]
    except KeyError as err:
        raise KeyError(f"Category {category} not found") from err

    if vendor is None:
        capability = capabilities[capability]
    else:
        try:
            vendors = capabilities[capability]
        except KeyError as err:
            raise KeyError(f"Capability {capability} not found") from err
        try:
            capability = vendors[vendor]
        except KeyError as err:
            raise KeyError(f"Vendor {vendor} not found") from err
    return capability
