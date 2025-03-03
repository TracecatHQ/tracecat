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
    extract_md5,
    extract_sha1,
    extract_sha256,
    extract_sha512,
)
from .ip import (
    extract_ip,
    extract_ipv4,
    extract_ipv6,
)
from .mac import extract_mac
from .url import extract_urls

__all__ = [
    "extract_asns",
    "extract_cves",
    "extract_domains",
    "extract_emails",
    "extract_md5",
    "extract_sha1",
    "extract_sha256",
    "extract_sha512",
    "extract_ip",
    "extract_ipv4",
    "extract_ipv6",
    "extract_mac",
    "extract_urls",
    "normalize_email",
]
