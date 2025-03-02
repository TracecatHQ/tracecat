"""IoC extractors with validation (if supported by Pydantic).

References:
- https://docs.iocparser.com/
- https://github.com/InQuest/iocextract/blob/master/iocextract.py
"""

from .asn import extract_asns
from .cve import extract_cves
from .domain import extract_domains
from .email import extract_emails, normalize_email
from .hash import (
    extract_md5_hashes,
    extract_sha1_hashes,
    extract_sha256_hashes,
    extract_sha512_hashes,
)
from .ip_address import (
    extract_ip_addresses,
    extract_ipv4_addresses,
    extract_ipv6_addresses,
)
from .mac_address import extract_mac_addresses
from .url import extract_urls

__all__ = [
    "extract_asns",
    "extract_cves",
    "extract_domains",
    "extract_emails",
    "extract_md5_hashes",
    "extract_sha1_hashes",
    "extract_sha256_hashes",
    "extract_sha512_hashes",
    "extract_ip_addresses",
    "extract_ipv4_addresses",
    "extract_ipv6_addresses",
    "extract_mac_addresses",
    "extract_urls",
    "normalize_email",
]
