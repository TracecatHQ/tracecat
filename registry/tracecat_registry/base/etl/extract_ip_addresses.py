import ipaddress
import itertools
import re

IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"

IPV6_REGEX = (
    r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|"
    r"(?=:)(?:(?::(?:[0-9a-fA-F]{1,4})){1,7}|:)|"
    r"(?:[0-9a-fA-F]{1,4}:){1,7}:|"
    r"(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|"
    r"[0-9a-fA-F]{1,4}:(?:(?::[0-9a-fA-F]{1,4}){1,6})\b"
)

def extract_ipv4_addresses(texts):
    """Extrait les adresses IPv4 uniques à partir d'une liste de textes."""
    if isinstance(texts, str):
        texts = [texts]

    ip_addresses = itertools.chain.from_iterable(
        re.findall(IPV4_REGEX, text) for text in texts
    )

    valid_ips = set()
    for ip in ip_addresses:
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.version == 4:
                valid_ips.add(str(ip_obj))
        except ValueError:
            continue  # Ignorer les IP invalides

    return list(valid_ips)


def extract_ipv6_addresses(texts):
    """Extract unique IPv6 addresses from a list of strings."""
    if isinstance(texts, str):
        texts = [texts]

    ip_addresses = itertools.chain.from_iterable(
        re.findall(IPV6_REGEX, text, re.IGNORECASE) for text in texts
    )

    valid_ips = set()
    for ip in ip_addresses:
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.version == 6:
                valid_ips.add(str(ip_obj))
        except ValueError:
            continue  # Ignorer les IP invalides

    return list(valid_ips)
