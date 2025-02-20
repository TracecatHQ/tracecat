import ipaddress
import itertools
import re
from typing import Annotated

from pydantic import Field

from tracecat_registry import registry

IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"


@registry.register(
    default_title="Extract IPv4 addresses",
    description="Extract unique IPv4 addresses from a list of strings.",
    namespace="etl.extraction",
    display_group="Data Extraction",
)
def extract_ipv4_addresses(
    texts: Annotated[
        str | list[str],
        Field(..., description="Text or list of text to extract IP addresses from"),
    ],
) -> list[str]:
    """Extract unique IPv4 addresses from a list of strings."""

    if isinstance(texts, str):
        texts = [texts]

    ip_addresses = itertools.chain.from_iterable(
        re.findall(IPV4_REGEX, text) for text in texts
    )

    # Validate IP addresses
    valid_ips = set()
    for ip in ip_addresses:
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.version == 4:
                valid_ips.add(str(ip_obj))
        except ValueError:
            continue  # Skip invalid IP addresses

    return list(valid_ips)
