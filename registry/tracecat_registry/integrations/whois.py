"""WHOIS domain lookup integration using python-whois library."""

import datetime
import re
from typing import Annotated, Any

import whois
from ipwhois import IPWhois
from typing_extensions import Doc

from tracecat_registry import registry


def _validate_domain(domain: str) -> str:
    """Validate and normalize domain name.

    Args:
        domain: Domain name to validate

    Returns:
        Normalized domain name

    Raises:
        ValueError: If domain is invalid
    """
    if not domain or not domain.strip():
        raise ValueError("Domain cannot be empty")

    # Remove leading/trailing whitespace
    domain = domain.strip()

    # Remove protocol if present
    if domain.startswith(("http://", "https://")):
        domain = domain.split("://", 1)[1]

    # Remove path if present
    if "/" in domain:
        domain = domain.split("/", 1)[0]

    # Remove port if present
    if ":" in domain:
        domain = domain.split(":", 1)[0]

    # Basic domain validation using regex
    domain_pattern = re.compile(
        r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
    )

    if not domain_pattern.match(domain):
        raise ValueError(f"Invalid domain format: {domain}")

    if len(domain) > 253:
        raise ValueError(f"Domain name too long: {domain}")

    return domain.lower()


@registry.register(
    default_title="WHOIS domain lookup",
    description="Perform a WHOIS lookup for a domain name to get registration information.",
    display_group="WHOIS",
    doc_url="https://pypi.org/project/python-whois/",
    namespace="tools.whois",
)
def lookup_domain(
    domain: Annotated[str, Doc("Domain name to lookup (e.g., 'example.com').")],
) -> dict[str, Any]:
    """Perform a WHOIS lookup for a domain name.

    Args:
        domain: Domain name to lookup

    Returns:
        WHOIS information for the domain

    Raises:
        ValueError: If domain is invalid
        RuntimeError: If WHOIS lookup fails
    """
    # Validate and normalize domain
    domain = _validate_domain(domain)
    try:
        whois_data = whois.whois(domain)
        result = dict(whois_data)  # Convert to plain dict
    except Exception as e:
        raise RuntimeError(f"WHOIS lookup failed for domain '{domain}': {str(e)}")

    # Add metadata
    result["_metadata"] = {
        "domain": domain,
        "lookup_successful": True,
        "timestamp": datetime.datetime.now().isoformat(),
    }

    return result


@registry.register(
    default_title="WHOIS IP lookup",
    description="Perform a WHOIS lookup for an IP address to get network information.",
    display_group="WHOIS",
    doc_url="https://pypi.org/project/python-whois/",
    namespace="tools.whois",
)
def lookup_ip(
    ip_address: Annotated[str, Doc("IP address to lookup (e.g., '8.8.8.8').")],
) -> dict[str, Any]:
    """Perform a WHOIS lookup for an IP address.

    Args:
        ip_address: IP address to lookup

    Returns:
        WHOIS information for the IP address

    Raises:
        ValueError: If IP address is invalid
        RuntimeError: If WHOIS lookup fails
    """
    # Basic IP validation
    if not ip_address or not ip_address.strip():
        raise ValueError("IP address cannot be empty")

    ip_address = ip_address.strip()

    # Simple IP validation using regex
    ip_pattern = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    )

    if not ip_pattern.match(ip_address):
        raise ValueError(f"Invalid IP address format: {ip_address}")

    try:
        # Perform WHOIS lookup
        whois_data = IPWhois(ip_address)

        if whois_data is None:
            raise RuntimeError(f"WHOIS lookup returned no data for IP: {ip_address}")

        # Serialize the data
        result = whois_data.lookup_rdap()
        result = dict(result)

        # Add metadata
        result["_metadata"] = {
            "ip_address": ip_address,
            "lookup_successful": True,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        return result

    except whois.parser.PywhoisError as e:
        raise RuntimeError(f"WHOIS lookup failed for IP '{ip_address}': {str(e)}")
    except Exception as e:
        raise RuntimeError(
            f"Unexpected error during WHOIS lookup for IP '{ip_address}': {str(e)}"
        )
