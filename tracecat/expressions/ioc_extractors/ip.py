"""Functions for extracting IPv4 and IPv6 addresses from a string.

Supports extracting from regular IPv4 and IPv6 addresses,
as well as the following defanged variants:

IPv4:
- Full brackets: [1.1.1.1]
- Brackets-dot: 1[.]1[.]1[.]1
- Parentheses-dot: 1(.)1(.)1(.)1
- Escaped dot: 1\\.1\\.1\\.1
- Text-bracket-dot: 1[dot]1[dot]1[dot]1
- Space-dot-space: 1 dot 1 dot 1 dot 1

IPv6:
- Full brackets: [2001:db8::1]
- Brackets-colon: 2001[:]db8[:]85a3[:]8d3[:]1319[:]8a2e[:]370[:]7348
- Parentheses-colon: 2001(:)db8(:)85a3(:)8d3(:)1319(:)8a2e(:)370(:)7348
- Escaped colon: 2001\\:db8\\:85a3\\:8d3\\:1319\\:8a2e\\:370\\:7348
- Text-bracket-colon: 2001[colon]db8[colon]85a3[colon]8d3[colon]1319[colon]8a2e[colon]370[colon]7348
- Space-colon-space: 2001 colon db8 colon 85a3 colon 8d3 colon 1319 colon 8a2e colon 370 colon 7348
"""

import functools
import ipaddress
import re
from enum import Enum, auto
from functools import lru_cache
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


# Define IP types enum
class IPType(Enum):
    """Enum for IP address types."""

    IPV4 = auto()
    IPV6 = auto()


@lru_cache(maxsize=1024)
def is_ipv4(ip: str) -> bool:
    """Check if a string is a valid IPv4 address."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except (AddressValueError, ValueError):
        return False


@lru_cache(maxsize=1024)
def is_ipv6(ip: str) -> bool:
    """Check if a string is a valid IPv6 address."""
    try:
        ipaddress.IPv6Address(ip)
        return True
    except (AddressValueError, ValueError):
        return False


def is_ip(ip: str) -> bool:
    """Check if a string is a valid IP address (either IPv4 or IPv6)."""
    return is_ipv4(ip) or is_ipv6(ip)


def extract_ipv4(text: str, include_defanged: bool = False) -> list[str]:
    """Extract unique IPv4 addresses from a string."""

    matched_ips = re.findall(IPV4_REGEX, text)
    if include_defanged:
        # Normalize the text
        replacements = {
            "[.]": ".",
            "(.)": ".",
            "\\.": ".",
            "[dot]": ".",
            " dot ": ".",
        }
        normalized_text = functools.reduce(
            lambda substring, replacement: substring.replace(
                replacement[0], replacement[1]
            ),
            replacements.items(),
            text,
        )
        matched_normalized_ips = re.findall(IPV4_REGEX, normalized_text)
        matched_ips.extend(matched_normalized_ips)

    unique_ips = list({ip for ip in matched_ips if is_ipv4(ip)})
    return unique_ips


def extract_ipv6(text: str, include_defanged: bool = False) -> list[str]:
    """Extract unique IPv6 addresses from a string. Includes defanged variants as an option."""

    matched_ips = re.findall(IPV6_REGEX, text)

    if include_defanged:
        # Normalize the text
        replacements = {
            "[:]": ":",
            "(:)": ":",
            "\\:": ":",
            "[colon]": ":",
            " colon ": ":",
        }
        normalized_text = functools.reduce(
            lambda substring, replacement: substring.replace(
                replacement[0], replacement[1]
            ),
            replacements.items(),
            text,
        )
        matched_normalized_ips = re.findall(IPV6_REGEX, normalized_text)
        matched_ips.extend(matched_normalized_ips)

    unique_ips = list({ip for ip in matched_ips if is_ipv6(ip)})
    return unique_ips


def extract_ip(text: str, include_defanged: bool = False) -> list[str]:
    """Extract unique IPv4 and IPv6 addresses from a string."""
    ipv4_addrs = extract_ipv4(text, include_defanged)
    ipv6_addrs = extract_ipv6(text, include_defanged)
    return ipv4_addrs + ipv6_addrs
