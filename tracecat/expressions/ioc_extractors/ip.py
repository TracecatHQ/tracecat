"""Functions for extracting IPv4 and IPv6 addresses from a string.

Supports extracting from regular IPv4 and IPv6 addresses,
as well as the following defanged variants:

IPv4:
- 1[.]1[.]1[.]1, where "." is defanged as "[.]"
- 1(.)1(.)1(.)1, where "." is defanged as "(.)"
- 1\\.1\\.1\\.1, where "." is defanged as "\\." (single backslash)

IPv6:
- 2001[:]db8[:]:[:]1, where ":" is defanged as "[:]"
- 2001(:)db8(:)(:)1, where ":" is defanged as "(:)"
- 2001\\:db8\\:\\:1, where ":" is defanged as "\\:" (single backslash)
"""

import ipaddress
import re
from ipaddress import AddressValueError

# IP ADDRESS
IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"

# Comprehensive IPv6 regex that covers:
# 1. Full format: 2001:0db8:85a3:0000:0000:8a2e:0370:7334
# 2. Compressed format: 2001:db8::1
# 3. Bracketed format: [2001:db8::1]
# 4. Includes uppercase and lowercase hex digits
IPV6_REGEX = (
    # Full format IPv6 without brackets
    r"(?:\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b)"
    # Compressed IPv6 format without brackets (with :: notation)
    r"|(?:\b(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::"
    r"(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\b)"
    # Full format IPv6 with brackets
    r"|(?:\[(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\])"
    # Compressed IPv6 format with brackets (with :: notation)
    r"|(?:\[(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::"
    r"(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\])"
)


def is_ipv4(ip: str) -> bool:
    """Check if a string is a valid IPv4 address."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except AddressValueError:
        return False


def is_ipv6(ip: str) -> bool:
    """Check if a string is a valid IPv6 address."""
    try:
        ipaddress.IPv6Address(ip)
        return True
    except AddressValueError:
        return False


def is_ip(ip: str) -> bool:
    """Check if a string is a valid IP address (either IPv4 or IPv6)."""
    return is_ipv4(ip) or is_ipv6(ip)


def extract_ipv4(text: str) -> list[str]:
    """Extract unique IPv4 addresses from a string."""
    unique_ips = set()
    for ip in re.findall(IPV4_REGEX, text):
        try:
            ipaddress.IPv4Address(ip)
            unique_ips.add(ip)
        except AddressValueError:
            pass
    return list(unique_ips)


def extract_ipv6(text: str) -> list[str]:
    """Extract unique IPv6 addresses from a string."""

    # This function preserves the original format of the IPv6 address
    # while still validating it. It handles:
    # - Full format addresses (with all segments and leading zeros)
    # - Compressed addresses (with :: notation)
    # - Addresses in brackets
    # - Mixed case hex digits

    unique_ips = set()

    for match in re.finditer(IPV6_REGEX, text):
        ip_str = match.group(0)

        if ip_str.startswith("[") and ip_str.endswith("]"):
            ip_str = ip_str[1:-1]

        try:
            ipaddress.IPv6Address(ip_str)
            unique_ips.add(ip_str)
        except AddressValueError:
            pass

    return list(unique_ips)


def extract_ip(text: str) -> list[str]:
    """Extract unique IPv4 and IPv6 addresses from a string."""
    ipv4_addrs = extract_ipv4(text)
    ipv6_addrs = extract_ipv6(text)
    return ipv4_addrs + ipv6_addrs
