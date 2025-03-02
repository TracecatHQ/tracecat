import ipaddress
import re
from ipaddress import AddressValueError

# IP ADDRESS
IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"

# Comprehensive IPv6 regex that covers:
# 1. Full format: 2001:0db8:85a3:0000:0000:8a2e:0370:7334
# 2. Compressed format: 2001:db8::1
# 3. Bracketed format: [2001:db8::1]
# Including uppercase and lowercase hex digits
IPV6_REGEX = r"(?:\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b)|(?:\b(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\b)|(?:\[(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\])|(?:\[(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\])"


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
