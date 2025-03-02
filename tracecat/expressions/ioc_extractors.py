"""IoC extractors with validation (if supported by Pydantic).

References:
- https://docs.iocparser.com/
- https://github.com/InQuest/iocextract/blob/master/iocextract.py
"""

import ipaddress
import re
from ipaddress import AddressValueError

from pydantic import BaseModel, EmailStr, HttpUrl, ValidationError, field_validator
from pydantic_extra_types.domain import DomainStr
from pydantic_extra_types.mac_address import MacAddress

# ASN (Autonomous System Number)
ASN_REGEX = r"\bAS\d+\b"


def extract_asns(text: str) -> list[str]:
    """Extract Autonomous System Numbers, e.g. AS1234, from a string."""
    # Use a set to ensure uniqueness
    return list(set(re.findall(ASN_REGEX, text)))


# DOMAIN
# This regex aims to match domain names while avoiding matching URLs
DOMAIN_REGEX = r"(?<![:/\w])(?:(?:xn--[a-zA-Z0-9]+|[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*)(?![:/\w])"


class DomainModel(BaseModel):
    domain: DomainStr


def extract_domains(text: str) -> list[str]:
    """Extract domain names, e.g. example.com, from a string."""
    unique_domains = set()
    for domain in re.findall(DOMAIN_REGEX, text):
        try:
            validated_domain = DomainModel(domain=domain).domain
            unique_domains.add(validated_domain)
        except ValidationError:
            pass
    return list(unique_domains)


# URL
# Match URLs including paths and query parameters
URL_REGEX = r"https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)(?<![?&=/#.])"


class UrlModel(BaseModel):
    url: HttpUrl


def extract_urls(text: str) -> list[str]:
    """Extract unique URLs from a string."""
    # Use a set to deduplicate URLs
    url_matches = set(re.findall(URL_REGEX, text))
    result = []
    for url in url_matches:
        try:
            # Validate with pydantic but preserve original format
            UrlModel(url=url)
            result.append(url)
        except ValidationError:
            pass
    return result


# IP ADDRESS
IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"

# Comprehensive IPv6 regex that covers:
# 1. Full format: 2001:0db8:85a3:0000:0000:8a2e:0370:7334
# 2. Compressed format: 2001:db8::1
# 3. Bracketed format: [2001:db8::1]
# Including uppercase and lowercase hex digits
IPV6_REGEX = r"(?:\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b)|(?:\b(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\b)|(?:\[(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\])|(?:\[(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\])"


def extract_ipv4_addresses(text: str) -> list[str]:
    """Extract unique IPv4 addresses from a string."""
    unique_ips = set()
    for ip in re.findall(IPV4_REGEX, text):
        try:
            ipaddress.IPv4Address(ip)
            unique_ips.add(ip)
        except AddressValueError:
            pass
    return list(unique_ips)


def extract_ipv6_addresses(text: str) -> list[str]:
    """Extract unique IPv6 addresses from a string.

    This function preserves the original format of the IPv6 address
    while still validating it. It handles:
    - Full format addresses (with all segments and leading zeros)
    - Compressed addresses (with :: notation)
    - Addresses in brackets
    - Mixed case hex digits
    """
    result = []
    seen = set()  # Track normalized versions to avoid duplicates

    # Find all potential IPv6 addresses
    for match in re.finditer(IPV6_REGEX, text):
        ip_str = match.group(0)

        # If the address is in brackets, remove them for validation
        if ip_str.startswith("[") and ip_str.endswith("]"):
            ip_str = ip_str[1:-1]

        try:
            # Validate the IP but don't normalize it
            ip_obj = ipaddress.IPv6Address(ip_str)
            normalized = str(ip_obj).lower()

            # If we've already seen this IP (in a different format), skip it
            if normalized in seen:
                continue

            seen.add(normalized)
            result.append(ip_str)
        except AddressValueError:
            pass

    return result


def extract_ip_addresses(text: str) -> list[str]:
    """Extract unique IPv4 and IPv6 addresses from a string."""
    ipv4_addrs = extract_ipv4_addresses(text)
    ipv6_addrs = extract_ipv6_addresses(text)
    return ipv4_addrs + ipv6_addrs


# MAC ADDRESS
MAC_REGEX = r"(?<![\d:A-Fa-f-])(?:[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})(?![\d:A-Fa-f-])"


class MacAddressModel(BaseModel):
    mac_address: MacAddress

    @field_validator("mac_address")
    def normalize_format(cls, v):
        parts = str(v).replace(":", "").replace("-", "")
        return ":".join(parts[i : i + 2].upper() for i in range(0, 12, 2))


def extract_mac_addresses(text: str) -> list[str]:
    """Extract MAC addresses from a string.

    Examples: 00:11:22:33:44:55, 00-11-22-33-44-55
    """
    unique_macs = set()
    for mac in re.findall(MAC_REGEX, text):
        try:
            validated_mac = MacAddressModel(mac_address=mac).mac_address
            unique_macs.add(validated_mac)
        except ValidationError:
            pass
    return list(unique_macs)


# EMAIL
EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"


class EmailModel(BaseModel):
    email: EmailStr


def normalize_email(email: str) -> str:
    """Convert sub-addressed email to a normalized email address.

    This function:
    1. Converts the email to lowercase
    2. Removes the subaddress part (everything after + in the local part)

    Example: User.Name+Newsletter@Example.COM -> user.name@example.com
    """
    email = email.lower()
    local_part, domain = email.split("@")
    local_part = local_part.split("+")[0]
    return f"{local_part}@{domain}"


def extract_emails(text: str, normalize: bool = False) -> list[str]:
    """Extract unique emails from a string.

    Args:
        text: The string to extract emails from
        normalize: Whether to normalize emails by removing subaddresses
                  and converting to lowercase

    Returns:
        A list of unique, validated email addresses
    """
    potential_emails = re.findall(EMAIL_REGEX, text)
    unique_emails = set()

    for email in potential_emails:
        try:
            # First validate the email with Pydantic
            validated_email = EmailModel(email=email).email

            # Apply normalization if requested
            if normalize:
                validated_email = normalize_email(validated_email)

            unique_emails.add(validated_email)
        except ValidationError:
            pass

    return list(unique_emails)


# HASH
MD5_REGEX = r"\b[a-fA-F0-9]{32}\b"
SHA1_REGEX = r"\b[a-fA-F0-9]{40}\b"
SHA256_REGEX = r"\b[a-fA-F0-9]{64}\b"
SHA512_REGEX = r"\b[a-fA-F0-9]{128}\b"


def extract_md5_hashes(text: str) -> list[str]:
    """Extract MD5 hashes from a string."""
    # MD5 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(MD5_REGEX, text)))


def extract_sha1_hashes(text: str) -> list[str]:
    """Extract SHA1 hashes from a string."""
    # SHA1 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(SHA1_REGEX, text)))


def extract_sha256_hashes(text: str) -> list[str]:
    """Extract SHA256 hashes from a string."""
    # SHA256 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(SHA256_REGEX, text)))


def extract_sha512_hashes(text: str) -> list[str]:
    """Extract SHA512 hashes from a string."""
    # SHA512 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(SHA512_REGEX, text)))


# CVE (Common Vulnerabilities and Exposures)
CVE_REGEX = r"CVE-\d{4}-\d{4,7}"


def extract_cves(text: str) -> list[str]:
    """Extract CVE IDs, such as CVE-2021-34527, from a string."""
    # CVEs don't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(CVE_REGEX, text)))
